"""Load a railway folder, resolve timetables against the track, and check it."""

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .geo import METRES_PER_MILE, Line

SNAP_WARN_METRES = 50
SPEED_RANGE_MPH = (5, 60)
TIME_RE = re.compile(r"^(~?)(\d{1,2}):(\d{2})$")


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


@dataclass
class StopTime:
    station: Station
    time: int | None      # seconds after midnight; None until estimated
    kind: str             # printed | reconstructed | estimated
    regular: bool         # False for flag stops
    mile: float | None    # printed milepost, if the card has one


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


def parse_time(cell):
    m = TIME_RE.match(cell)
    if not m:
        return None
    return int(m[2]) * 3600 + int(m[3]) * 60, ("reconstructed" if m[1] else "printed")


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
        s = Station(p["id"], p["name"], lon, lat, p.get("aliases", []))
        s.measure, s.offset = track.locate((lon, lat))
        if s.offset > SNAP_WARN_METRES:
            report.warn(f"station {s.name}: {s.offset:.0f} m from the track")
        stations[s.id] = s
        for n in [s.name, *s.aliases]:
            by_name[n.casefold()] = s

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

    rw = Railway(meta["id"], meta, track, stations, eras)
    for era in eras:
        for trip in era.trips:
            if check_trip(era, trip, report):
                estimate_times(trip)
                check_speeds(era, trip, report)
    return rw


def read_timetable(path, by_name, report):
    """Read a card-shaped grid: stations down, train numbers across.

    A blank cell means the train serves the station but no time is printed;
    "-" means it does not serve the station at all.
    """
    with open(path, newline="", encoding="utf8") as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in rows[0].keys() if c not in ("station", "mile", "stop")]
    trips = {c: Trip(c, path.name) for c in cols}
    for row in rows:
        station = by_name.get(row["station"].strip().casefold())
        if station is None:
            report.error(f"{path.name}: unknown station {row['station']!r}")
            continue
        mile = float(row["mile"]) if row.get("mile", "").strip() else None
        regular = row.get("stop", "").strip().upper() == "S"
        for c in cols:
            cell = row[c].strip()
            if cell == "-":
                continue
            parsed = parse_time(cell) if cell else (None, "estimated")
            if parsed is None:
                report.error(f"{path.name}: train {c} at {station.name}: can't read {cell!r}")
                continue
            trips[c].stops.append(StopTime(station, parsed[0], parsed[1], regular, mile))
    # The card may read down or up; put every trip in time order.
    for t in trips.values():
        known = [s.time for s in t.stops if s.time is not None]
        if len(known) >= 2 and known[0] > known[-1]:
            t.stops.reverse()
    return list(trips.values())


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
    known = [(s.station.name, s.time) for s in trip.stops if s.time is not None]
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
            s.time = round((a.time + u * (b.time - a.time)) / 60) * 60


def check_speeds(era, trip, report):
    lo, hi = SPEED_RANGE_MPH
    for a, b in zip(trip.stops, trip.stops[1:]):
        miles = abs(b.station.measure - a.station.measure) / METRES_PER_MILE
        minutes = (b.time - a.time) / 60
        mph = miles / (minutes / 60) if minutes > 0 else float("inf")
        if not lo <= mph <= hi:
            shown = "no time between them" if minutes <= 0 else f"{mph:.0f} mph"
            report.warn(f"era {era.id}, train {trip.number}: {a.station.name} -> {b.station.name} "
                        f"{miles:.2f} mi in {minutes:.0f} min ({shown})")
