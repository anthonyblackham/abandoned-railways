"""Write a GTFS feed per era and the JSON the map viewer loads."""

import csv
import io
import json
import zipfile

from .geo import haversine

SITE_URL = "https://anthonyblackham.com/abandoned-railways/"
PUBLISHER = "Abandoned Railways"
CONTACT_URL = "https://github.com/anthonyblackham/abandoned-railways/issues"
# Every era runs "today": the point is to replay the timetable against the current clock.
SERVICE_START, SERVICE_END = "20000101", "20991231"
DAYS = {
    "daily": [1, 1, 1, 1, 1, 1, 1],
    "weekdays": [1, 1, 1, 1, 1, 0, 0],
    "saturdays": [0, 0, 0, 0, 0, 1, 0],
    "sundays": [0, 0, 0, 0, 0, 0, 1],
}


def hhmmss(seconds):
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def table(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def era_ids(rw, era):
    return f"{rw.id}-{era.id}"


def shape_for(rw, era, trip):
    """Each era and direction gets one shape, spanning the furthest stops any trip reaches."""
    trips = [t for t in era.trips if t.direction == trip.direction]
    ms = [s.station.measure for t in trips for s in t.stops]
    lo, hi = min(ms), max(ms)
    start, end = (lo, hi) if trip.direction == 0 else (hi, lo)
    return f"{era_ids(rw, era)}-{trip.direction}", start, end


def gtfs(rw, era):
    """Return the bytes of a GTFS zip for one era."""
    m = rw.meta
    feed = era_ids(rw, era)
    stops_used = {s.station.id: s.station for t in era.trips for s in t.stops}

    agency = [[rw.id, m["name"], SITE_URL, m["timezone"], "en"]]
    stops = [[s.id, s.name, f"{s.lat:.6f}", f"{s.lon:.6f}"]
             for s in sorted(stops_used.values(), key=lambda s: s.measure)]
    colour = m.get("colour", "#444444").lstrip("#")
    routes = [[feed, rw.id, "", era.label,
               f"Historical {era.id} timetable replayed daily. This railway no longer runs.",
               m.get("route_type", 2), colour, "FFFFFF"]]
    calendar = [[feed, *DAYS[era.days], SERVICE_START, SERVICE_END]]

    trips, stop_times, shapes = [], [], {}
    for t in era.trips:
        shape_id, start, end = shape_for(rw, era, t)
        if shape_id not in shapes:
            pts = rw.track.slice(start, end)
            dist, rows = 0.0, []
            for i, p in enumerate(pts):
                if i:
                    dist += haversine(pts[i - 1], p)
                rows.append([shape_id, f"{p[1]:.6f}", f"{p[0]:.6f}", i, f"{dist:.1f}"])
            shapes[shape_id] = rows
        trip_id = f"{feed}-{t.number}"
        trips.append([feed, feed, trip_id, t.stops[-1].station.name, t.number, t.direction, shape_id])
        for seq, s in enumerate(t.stops, 1):
            flag = 0 if s.regular else 3
            stop_times.append([trip_id, hhmmss(s.time), hhmmss(s.time), s.station.id, seq, flag, flag,
                               f"{abs(s.station.measure - start):.1f}", 1 if s.kind == "printed" else 0])

    feed_info = [[PUBLISHER, SITE_URL, "en", SERVICE_START, SERVICE_END, era.id, CONTACT_URL]]

    files = {
        "agency.txt": table(agency, ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"]),
        "stops.txt": table(stops, ["stop_id", "stop_name", "stop_lat", "stop_lon"]),
        "routes.txt": table(routes, ["route_id", "agency_id", "route_short_name", "route_long_name",
                                     "route_desc", "route_type", "route_color", "route_text_color"]),
        "trips.txt": table(trips, ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name",
                                   "direction_id", "shape_id"]),
        "stop_times.txt": table(stop_times, ["trip_id", "arrival_time", "departure_time", "stop_id",
                                             "stop_sequence", "pickup_type", "drop_off_type",
                                             "shape_dist_traveled", "timepoint"]),
        "calendar.txt": table(calendar, ["service_id", "monday", "tuesday", "wednesday", "thursday",
                                         "friday", "saturday", "sunday", "start_date", "end_date"]),
        "shapes.txt": table([r for rows in shapes.values() for r in rows],
                            ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"]),
        "feed_info.txt": table(feed_info, ["feed_publisher_name", "feed_publisher_url", "feed_lang",
                                           "feed_start_date", "feed_end_date", "feed_version", "feed_contact_url"]),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def mileposts(rw, era):
    """Place each printed milepost on the track (linear referencing).

    Calibration points pin a printed mile to a station's real position; the
    scale is stretched piecewise between them. Without any, the two furthest
    apart stations that carry a milepost are used.
    """
    miles = {}
    for t in era.trips:
        for s in t.stops:
            if s.mile is not None:
                miles[s.station.id] = (s.station, s.mile)
    if len(miles) < 2:
        return []
    by_name = {st.name.casefold(): st for st in rw.stations.values()}
    if era.calibration:
        cal = [(c["mile"], by_name[c["station"].casefold()].measure) for c in era.calibration]
    else:
        ends = sorted(miles.values(), key=lambda sm: sm[1])
        cal = [(ends[0][1], ends[0][0].measure), (ends[-1][1], ends[-1][0].measure)]
    cal.sort()

    def measure_at(mile):
        i = 1
        while i < len(cal) - 1 and mile > cal[i][0]:
            i += 1
        (m0, x0), (m1, x1) = cal[i - 1], cal[i]
        return x0 + (mile - m0) * (x1 - x0) / (m1 - m0)

    out = []
    for station, mile in sorted(miles.values(), key=lambda sm: sm[1]):
        lon, lat = rw.track.interpolate(measure_at(mile))
        out.append({"station": station.id, "mile": mile, "lon": round(lon, 6), "lat": round(lat, 6)})
    return out


def web(rw):
    """The viewer's data for one railway. Times are seconds after local midnight."""
    m = rw.meta
    stations = sorted(rw.stations.values(), key=lambda s: s.measure)
    index = {s.id: i for i, s in enumerate(stations)}
    lons = [c[0] for c in rw.track.coords]
    lats = [c[1] for c in rw.track.coords]
    return {
        "id": rw.id,
        "name": m["name"],
        "colour": m.get("colour", "#444444"),
        "timezone": m["timezone"],
        "opened": str(m.get("opened", "")),
        "closed": str(m.get("closed", "")),
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "track": [[round(x, 6), round(y, 6)] for x, y in rw.track.coords],
        "trackMeasures": [round(v, 1) for v in rw.track.measures],
        "stations": [{"id": s.id, "name": s.name, "lon": s.lon, "lat": s.lat, "m": round(s.measure, 1)}
                     for s in stations],
        "eras": [{
            "id": e.id,
            "label": e.label,
            "days": e.days,
            "source": m.get("sources", {}).get(e.source, e.source),
            "notes": e.notes,
            "feed": f"feeds/{era_ids(rw, e)}.zip",
            "trips": [{
                "number": t.number,
                "direction": t.direction,
                "headsign": t.stops[-1].station.name,
                # [station index, seconds, flag stop, time kind]
                "stops": [[index[s.station.id], s.time, 0 if s.regular else 1, s.kind[0]] for s in t.stops],
            } for t in sorted(e.trips, key=lambda t: t.stops[0].time)],
            "mileposts": mileposts(rw, e),
        } for e in rw.eras],
    }


def write(rw, out):
    (out / "feeds").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for era in rw.eras:
        (out / "feeds" / f"{era_ids(rw, era)}.zip").write_bytes(gtfs(rw, era))
    data = web(rw)
    (out / "data" / f"{rw.id}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                                encoding="utf8")
    return {k: data[k] for k in ("id", "name", "colour", "opened", "closed", "bbox")} | {
        "eras": [{"id": e["id"], "label": e["label"]} for e in data["eras"]]}
