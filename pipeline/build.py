"""Write a GTFS feed per era and the JSON the map viewer loads."""

import csv
import io
import json
import math
import zipfile

from .geo import haversine

END_SLACK_METRES = 50   # a milepost this close past the end of the trace still shows
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
    """Each era, route and direction gets one shape, spanning the furthest stops any trip reaches."""
    trips = [t for t in era.trips if t.route == trip.route and t.direction == trip.direction]
    ms = [t.m(s) for t in trips for s in t.stops]
    lo, hi = min(ms), max(ms)
    start, end = (lo, hi) if trip.direction == 0 else (hi, lo)
    route = f"{trip.route}-" if len(rw.routes) > 1 else ""
    return f"{era_ids(rw, era)}-{route}{trip.direction}", start, end


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
            pts = rw.routes[t.route].slice(start, end)
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
            stop_times.append([trip_id, hhmmss(s.time), hhmmss(s.leaves), s.station.id, seq, flag, flag,
                               f"{abs(t.m(s) - start):.1f}", 1 if s.kind == "printed" else 0])

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
    """Whole-mile markers (MP 0, MP 1, ...) along the track.

    Mileposts come from the timetable when it prints them, otherwise from the
    plat index. Each station with a milepost pins that mile to the station's
    real position, and whole miles are interpolated between neighbouring
    stations (linear referencing), so the markers follow the railway's own
    numbering stretch by stretch. A railway.yaml era can give explicit
    calibration points instead.
    """
    by_name = {}
    for st in rw.stations.values():
        for n in (st.name, *st.aliases):
            by_name[n.casefold()] = st
    miles = {}
    for t in era.trips:
        for s in t.stops:
            if s.mile is not None:
                miles[s.station.id] = (s.station, s.mile)
    if len(miles) < 2:
        miles = {}
        for row in rw.plat_index:
            st = by_name.get(row["feature"].casefold())
            if st and (row["type"].lower() or "station") == "station" and row.get("milepost"):
                miles[st.id] = (st, float(row["milepost"]))
    stationed = stationed_mileposts(rw)
    if len(miles) < 2:
        return stationed
    if era.calibration:
        cal = [(c["mile"], by_name[c["station"].casefold()].measure) for c in era.calibration]
    else:
        cal = [(mile, st.measure) for st, mile in miles.values()]
    cal = sorted(set(cal))

    def measure_at(mile):
        i = 1
        while i < len(cal) - 1 and mile > cal[i][0]:
            i += 1
        (m0, x0), (m1, x1) = cal[i - 1], cal[i]
        return x0 + (mile - m0) * (x1 - x0) / (m1 - m0) if m1 != m0 else x0

    out = []
    exact = {m["mile"] for m in stationed}
    # Only between the outermost known mileposts: no guessing past the ends.
    for mile in range(math.ceil(cal[0][0]), math.floor(cal[-1][0]) + 1):
        if mile in exact:
            continue
        m = measure_at(mile)
        # Only where the traced track reaches, with a little slack at the ends.
        if -END_SLACK_METRES <= m <= rw.track.length + END_SLACK_METRES:
            lon, lat = rw.track.interpolate(m)
            out.append({"mile": mile, "lon": round(lon, 6), "lat": round(lat, 6)})
    return sorted(out + stationed, key=lambda m: m["mile"])


def stationed_mileposts(rw):
    """Mileposts the plat index places by engineering station.

    Within a stationing series, stations with known plat stationing tie
    station values to positions on the traced track; a milepost row is
    placed between them by its own station value.
    """
    from .load import parse_plat_station
    series = {}
    for st in rw.stations.values():
        if st.plat_station:
            series.setdefault(st.plat_series, []).append((parse_plat_station(st.plat_station), st.measure))
    out = []
    for row in rw.plat_index:
        if row["type"].lower() != "milepost" or not row["station"] or not row.get("milepost"):
            continue
        cal = sorted(series.get(row.get("series", ""), []))
        if len(cal) < 2:
            continue
        sta = parse_plat_station(row["station"])
        i = 1
        while i < len(cal) - 1 and sta > cal[i][0]:
            i += 1
        (s0, m0), (s1, m1) = cal[i - 1], cal[i]
        m = m0 + (sta - s0) * (m1 - m0) / (s1 - s0)
        lon, lat = rw.track.interpolate(m)
        out.append({"mile": round(float(row["milepost"])), "lon": round(lon, 6), "lat": round(lat, 6),
                    "station": row["station"]})
    return out


def web(rw):
    """The viewer's data for one railway. Times are seconds after local midnight."""
    m = rw.meta
    stations = sorted(rw.stations.values(), key=lambda s: s.measure)
    index = {s.id: i for i, s in enumerate(stations)}
    from .load import ROUTE_SNAP_METRES
    lons = [c[0] for line in rw.routes.values() for c in line.coords]
    lats = [c[1] for line in rw.routes.values() for c in line.coords]
    return {
        "id": rw.id,
        "name": m["name"],
        "colour": m.get("colour", "#444444"),
        "timezone": m["timezone"],
        "opened": str(m.get("opened") or ""),
        "closed": str(m.get("closed") or ""),
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "overlays": m.get("overlays", []),
        "track": [[round(x, 6), round(y, 6)] for x, y in rw.track.coords],
        "trackMeasures": [round(v, 1) for v in rw.track.measures],
        # Every route (the first is "track" above); trips name the one they run on.
        "routes": [{"id": rid, "track": [[round(x, 6), round(y, 6)] for x, y in line.coords],
                    "measures": [round(v, 1) for v in line.measures]} for rid, line in rw.routes.items()],
        "stations": [{"id": s.id, "name": s.name, "lon": s.lon, "lat": s.lat, "m": round(s.measure, 1),
                      "on": {rid: round(mo[0], 1) for rid, mo in s.on.items() if mo[1] <= ROUTE_SNAP_METRES},
                      **({"plat": s.plat_station} if s.plat_station else {}),
                      **({"mp": s.plat_milepost} if s.plat_milepost else {}),
                      **({"note": s.note} if s.note else {})}
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
                "route": t.route,
                "direction": t.direction,
                "headsign": t.stops[-1].station.name,
                # [station index, arrive, flag stop, time kind, leave]
                "stops": [[index[s.station.id], s.time, 0 if s.regular else 1, s.kind[0], s.leaves]
                          for s in t.stops],
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
    eras = [e["id"] for e in data["eras"]]
    featured = str(rw.meta.get("featured", eras[0] if eras else ""))
    return {k: data[k] for k in ("id", "name", "colour", "opened", "closed", "bbox")} | {
        "featured": featured,   # the era the network map runs for this line
        "eras": [{"id": e["id"], "label": e["label"]} for e in data["eras"]]}
