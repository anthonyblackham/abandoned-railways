"""Load a railway folder, resolve timetables against the track, and check it."""

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .geo import METRES_PER_MILE, Line

SNAP_WARN_METRES = 50
PLAT_WARN_METRES = 0.05 * METRES_PER_MILE   # per gap between neighbouring stations
SPEED_RANGE_MPH = (5, 60)
TIME_RE = re.compile(r"^(~?)(\d{1,2}):(\d{2})$")
PLAT_STATION_RE = re.compile(r"^(\d+)(?:\+(\d+(?:\.\d+)?))?$")   # "1059+23", or a bare "0"
METRES_PER_FOOT = 0.3048


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


@dataclass
class Station:
    id: str
    name: str
    lon: float
    lat: float
    aliases: list
    measure: float = 0.0   # metres along the track
    offset: float = 0.0    # metres off the track
    plat_station: str | None = None   # engineering station off the plat, e.g. "1059+23"
    plat_series: str = ""             # which stationing series it belongs to
    plat_milepost: str = ""           # milepost printed on the plat, if any
    note: str = ""                    # e.g. "Freight and log cars only"


@dataclass
class StopTime:
    station: Station
    time: int | None      # seconds after midnight; None until estimated
    kind: str             # printed | reconstructed | estimated
    regular: bool         # False for flag stops
    mile: float | None    # printed milepost, if the card has one
    departure: int | None = None   # when the card prints separate arrive and leave times

    @property
    def leaves(self):
        return self.departure if self.departure is not None else self.time


@dataclass
class Trip:
    number: str
    file: str
    stops: list = field(default_factory=list)

    @property
    def direction(self):
        """0 when the trip runs toward increasing measure, 1 otherwise."""
        return 0 if self.stops[-1].station.measure >= self.stops[0].station.measure else 1


@dataclass
class Era:
    id: str
    label: str
    days: str
    source: str
    notes: list
    calibration: list
    trips: list = field(default_factory=list)


@dataclass
class Railway:
    id: str
    meta: dict
    track: Line
    stations: dict   # id -> Station
    eras: list
    plat_index: list = field(default_factory=list)


def parse_time(cell):
    m = TIME_RE.match(cell)
    if not m:
        return None
    return int(m[2]) * 3600 + int(m[3]) * 60, ("reconstructed" if m[1] else "printed")


def parse_plat_station(value):
    """Engineering stationing ("1059+23" = 105,923 ft) to metres."""
    m = PLAT_STATION_RE.match(value.strip())
    if not m:
        return None
    return (int(m[1]) * 100 + float(m[2] or 0)) * METRES_PER_FOOT


def load(folder, report):
    folder = Path(folder)
    meta = yaml.safe_load((folder / "railway.yaml").read_text(encoding="utf8"))

    track_features = json.loads((folder / "track.geojson").read_text(encoding="utf8"))["features"]
    if len(track_features) != 1:
        report.error(f"track.geojson: expected one LineString, found {len(track_features)} features")
    track = Line(track_features[0]["geometry"]["coordinates"])

    stations, by_name = {}, {}
    for f in json.loads((folder / "stations.geojson").read_text(encoding="utf8"))["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        s = Station(p["id"], p["name"], lon, lat, p.get("aliases", []), note=p.get("note", ""))
        s.measure, s.offset = track.locate((lon, lat))
        if s.offset > SNAP_WARN_METRES:
            report.warn(f"station {s.name}: {s.offset:.0f} m from the track")
        stations[s.id] = s
        for n in [s.name, *s.aliases]:
            by_name[n.casefold()] = s

    plat_index = read_plat_index(folder / "plat_index.csv", meta["id"], by_name, report)
    check_plat_spacing(stations.values(), report)

    eras = []
    for e in meta.get("eras", []):
        era = Era(str(e["id"]), e["label"], e.get("days", "daily"), e.get("source", ""),
                  e.get("notes", []), e.get("calibration", []))
        files = sorted((folder / "timetables").glob(f"{era.id}-*.csv"))
        if not files:
            report.error(f"era {era.id}: no timetables/{era.id}-*.csv files")
        for path in files:
            era.trips += read_timetable(path, by_name, report)
        eras.append(era)

    rw = Railway(meta["id"], meta, track, stations, eras, plat_index)
    for era in eras:
        for trip in era.trips:
            if check_trip(era, trip, report):
                estimate_times(trip)
                check_speeds(era, trip, report)
    return rw


PLAT_COLUMNS = ["line", "feature", "type", "station", "milepost", "sheet", "revision", "notes"]


def read_plat_index(path, railway_id, by_name, report):
    """Read plat_index.csv: what the right-of-way plats say, one row per notation.

    Station rows give a station's engineering station, which the build then
    checks against the traced point. Other types (junction, bridge, crossing,
    equation...) are kept as an index for now.
    """
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in PLAT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            report.error(f"{path.name}: missing columns {', '.join(missing)}")
            return []
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in reader]
    for i, r in enumerate(rows, 2):
        where = f"{path.name} line {i}"
        if r["line"] != railway_id:
            report.error(f"{where}: line {r['line']!r} is not {railway_id!r}")
            continue
        kind = r["type"].lower() or "station"
        if kind == "equation":
            report.warn(f"{where}: station equations are recorded but not yet applied to the plat check")
            continue
        if r["station"] and parse_plat_station(r["station"]) is None:
            report.error(f"{where}: can't read station {r['station']!r} (expected e.g. 1059+23)")
            continue
        if kind == "station" and r["station"]:
            s = by_name.get(r["feature"].casefold())
            if s is None:
                report.warn(f"{where}: plat station {r['feature']!r} isn't in stations.geojson")
            elif s.plat_station and s.plat_station != r["station"]:
                report.warn(f"{where}: {s.name} is at Sta {s.plat_station} on another row; keeping the first")
            else:
                s.plat_station = r["station"]
                s.plat_series = r.get("series", "")
                s.plat_milepost = r.get("milepost", "")
    return rows


def check_plat_spacing(stations, report):
    """The plat's stationing is the reference for where stations stood.

    Compares the gap between neighbouring stations on the plat with the gap
    along the traced track. Only stations in the same stationing series are
    compared (a line surveyed in pieces can have several series, counting in
    either direction), so no zero point or direction needs to be known.
    """
    series = {}
    for s in stations:
        if s.plat_station:
            series.setdefault(s.plat_series, []).append(s)
    for members in series.values():
        members.sort(key=lambda s: s.measure)
        for a, b in zip(members, members[1:]):
            plat = abs(parse_plat_station(b.plat_station) - parse_plat_station(a.plat_station))
            traced = b.measure - a.measure
            if abs(traced - plat) > PLAT_WARN_METRES:
                report.warn(f"{a.name} -> {b.name}: {traced / METRES_PER_MILE:.2f} mi apart on the track but "
                            f"{plat / METRES_PER_MILE:.2f} mi on the plat (Sta {a.plat_station} to {b.plat_station})")


ROW_ROLE_RE = re.compile(r"^(.*?)\s*\((arrive|leave)\)$", re.I)


def read_timetable(path, by_name, report):
    """Read a card-shaped grid: stations down, train numbers across.

    Cells: "HH:MM" (24:xx after midnight), "~HH:MM" for a reconstructed or
    uncertain reading, blank when the train stops but no time is printed,
    "-" when it does not serve the station. A trailing "f" makes that train
    a flag stop there ("06:37f", or just "f"); a trailing "s" a regular stop.
    A station with separate arrive and leave rows on the card is written as
    two rows, "West Linn (arrive)" and "West Linn (leave)".
    """
    with open(path, newline="", encoding="utf8") as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in rows[0].keys() if c not in ("station", "mile", "stop")]
    entries = {c: [] for c in cols}
    for row in rows:
        name = row["station"].strip()
        m = ROW_ROLE_RE.match(name)
        if m:
            name = m[1]
        station = by_name.get(name.casefold())
        if station is None:
            report.error(f"{path.name}: unknown station {row['station']!r}")
            continue
        mile = float(row["mile"]) if row.get("mile", "").strip() else None
        default_regular = row.get("stop", "").strip().upper() == "S"
        for c in cols:
            cell = row[c].strip()
            if cell == "-":
                continue
            regular = default_regular
            if cell[-1:].lower() in ("f", "s"):
                regular = cell[-1].lower() == "s"
                cell = cell[:-1]
            parsed = parse_time(cell) if cell else (None, "estimated")
            if parsed is None:
                report.error(f"{path.name}: train {c} at {station.name}: can't read {row[c].strip()!r}")
                continue
            entries[c].append(StopTime(station, parsed[0], parsed[1], regular, mile))

    trips = []
    for c, stops in entries.items():
        if not stops:
            continue
        # The card may read down or up; put every trip in time order.
        known = [s.time for s in stops if s.time is not None]
        if len(known) >= 2 and known[0] > known[-1]:
            stops.reverse()
        # Arrive and leave rows for one station become one stop with a wait.
        merged = []
        for s in stops:
            prev = merged[-1] if merged else None
            if prev is not None and prev.station is s.station:
                times = sorted(t for t in (prev.time, prev.departure, s.time) if t is not None)
                if times:
                    prev.time, prev.departure = times[0], (times[-1] if times[-1] != times[0] else None)
                prev.regular = prev.regular or s.regular
                if s.kind == "reconstructed" or prev.kind == "estimated":
                    prev.kind = s.kind
                continue
            merged.append(s)
        trips.append(Trip(c, path.name, merged))
    return trips


def check_trip(era, trip, report):
    """Report problems; return False if the trip is too broken to build."""
    where = f"era {era.id}, train {trip.number}"
    if len(trip.stops) < 2:
        report.error(f"{where}: fewer than two stops")
        return False
    if trip.stops[0].time is None or trip.stops[-1].time is None:
        report.error(f"{where}: the first and last stops need printed times")
        return False
    before = len(report.errors)
    known = [(s.station.name, t) for s in trip.stops for t in (s.time, s.departure) if t is not None]
    for (a, ta), (b, tb) in zip(known, known[1:]):
        if tb < ta:
            report.error(f"{where}: time goes backwards from {a} to {b}")
    sign = 1 if trip.direction == 0 else -1
    for a, b in zip(trip.stops, trip.stops[1:]):
        if sign * (b.station.measure - a.station.measure) <= 0:
            report.error(f"{where}: {b.station.name} is not beyond {a.station.name} along the track")
    return len(report.errors) == before


def estimate_times(trip):
    """Fill blank cells by distance between the nearest known times."""
    known = [i for i, s in enumerate(trip.stops) if s.time is not None]
    for i0, i1 in zip(known, known[1:]):
        a, b = trip.stops[i0], trip.stops[i1]
        span = b.station.measure - a.station.measure
        for s in trip.stops[i0 + 1:i1]:
            u = (s.station.measure - a.station.measure) / span if span else 0
            s.time = round((a.leaves + u * (b.time - a.leaves)) / 60) * 60


def check_speeds(era, trip, report):
    lo, hi = SPEED_RANGE_MPH
    for a, b in zip(trip.stops, trip.stops[1:]):
        miles = abs(b.station.measure - a.station.measure) / METRES_PER_MILE
        minutes = (b.time - a.leaves) / 60
        mph = miles / (minutes / 60) if minutes > 0 else float("inf")
        if not lo <= mph <= hi:
            shown = "no time between them" if minutes <= 0 else f"{mph:.0f} mph"
            report.warn(f"era {era.id}, train {trip.number}: {a.station.name} -> {b.station.name} "
                        f"{miles:.2f} mi in {minutes:.0f} min ({shown})")
