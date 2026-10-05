"""Railroad alignments from plat curve data (coordinate geometry).

An alignment is a sequence of elements, each running from one station to the
next: tangents (straight) and circular curves (radius, turning left or right).
Stations are in feet; a curve's length is its arc length, so a curve's radius
and central angle must agree with its stationing (checked on load).

The alignment is built in a local frame (feet, starting at the origin heading
east), then placed on the map by a rigid fit (rotation + translation, no
scaling): roughly to reference geometry (`fit`), then exactly to control points
the user reads off georeferenced plats (`place`).

CSV columns: from_sta, to_sta, type (tangent | curve | equation), radius, delta, turn (L | R), sheet, notes

Control point CSV columns: label, easting_ft, northing_ft, station, offset_ft, sheet, notes.
offset_ft is the distance from the centreline, e.g. "15 right" (right of the direction of
stationing) or "0". A point with a station fixes position along and across the line; one
with an offset but no station only says how far it is from the line (e.g. clicks down the
middle of the old right-of-way strip); one with neither is a check, not used in the fit.
"""

import csv
import math
import re

from .load import parse_plat_station

FT = 0.3048


def dms(text):
    """'26°18'' or '26-18' -> degrees."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    return nums[0] + (nums[1] if len(nums) > 1 else 0) / 60 + (nums[2] if len(nums) > 2 else 0) / 3600


def _sta(text):
    """Station text to feet; a leading '-' (before the zero point) makes it negative."""
    text = text.strip()
    feet = parse_plat_station(text.lstrip("-")) / FT
    return -feet if text.startswith("-") else feet


def read(path):
    """Read an alignment CSV; returns (elements, problems)."""
    elements, problems = [], []
    with open(path, newline="", encoding="utf8") as f:
        for i, r in enumerate(csv.DictReader(f), 2):
            a, b = (_sta(r["from_sta"]), _sta(r["to_sta"]))
            e = {"from": a, "to": b, "type": r["type"].strip().lower(), "line": i,
                 "sheet": r.get("sheet", ""), "notes": r.get("notes", "")}
            if e["type"] == "curve":
                e["radius"] = float(r["radius"])
                e["delta"] = dms(r["delta"])
                e["turn"] = r["turn"].strip().upper()
                arc = e["radius"] * math.radians(e["delta"])
                if abs(arc - (b - a)) > 0.5:
                    problems.append(f"line {i}: curve {r['from_sta']}-{r['to_sta']}: radius {e['radius']} and "
                                    f"delta {r['delta']} give {arc:.2f} ft, stationing gives {b - a:.2f} ft")
            elements.append(e)
    for x, y in zip(elements, elements[1:]):
        if abs(x["to"] - y["from"]) > 0.01:
            problems.append(f"line {y['line']}: starts at {y['from']:.2f} but the previous element ends at {x['to']:.2f}")
    return elements, problems


def trace(elements, step=10.0):
    """Points (x, y, station) every `step` feet along the alignment, in a local frame."""
    x, y, h = 0.0, 0.0, 0.0     # heading in radians, 0 = east, counterclockwise positive
    pts = [(x, y, elements[0]["from"])]
    for e in elements:
        length = e["to"] - e["from"]
        n = max(1, math.ceil(length / step))
        if e["type"] == "equation":     # station equation: same point, the stationing jumps
            pts.append((x, y, e["to"]))
            continue
        if e["type"] == "tangent":
            for k in range(1, n + 1):
                s = length * k / n
                pts.append((x + s * math.cos(h), y + s * math.sin(h), e["from"] + s))
            x, y = pts[-1][0], pts[-1][1]
        else:
            sign = 1 if e["turn"] == "L" else -1
            r = e["radius"]
            cx, cy = x - sign * r * math.sin(h), y + sign * r * math.cos(h)   # centre of the curve
            for k in range(1, n + 1):
                a = length * k / n / r
                hh = h + sign * a
                pts.append((cx + sign * r * math.sin(hh), cy - sign * r * math.cos(hh), e["from"] + length * k / n))
            h += sign * length / r
            x, y = pts[-1][0], pts[-1][1]
    return pts


def fit(points, reference, iterations=60):
    """Rigid fit (rotation + translation) of `points` (x, y) to a reference polyline, by
    iterative closest point. Returns (transform function, rms feet, max feet)."""
    import numpy as np
    P = np.array([(p[0], p[1]) for p in points])
    ref = np.array(reference)
    seg_a, seg_b = ref[:-1], ref[1:]

    def closest(Q):
        d = seg_b - seg_a
        L2 = (d ** 2).sum(1)
        t = np.clip(((Q[:, None, :] - seg_a[None]) * d[None]).sum(2) / L2[None], 0, 1)
        C = seg_a[None] + t[..., None] * d[None]
        dist = np.linalg.norm(Q[:, None, :] - C, axis=2)
        j = dist.argmin(1)
        return C[np.arange(len(Q)), j], dist[np.arange(len(Q)), j]

    # Start: align the end-to-end direction of the points with the nearest stretch of reference.
    best = None
    for start_angle in np.radians(np.arange(0, 360, 15)):
        R = np.array([[math.cos(start_angle), -math.sin(start_angle)], [math.sin(start_angle), math.cos(start_angle)]])
        Q0 = P @ R.T
        T = ref.mean(0) - Q0.mean(0)
        Rt, tt = R, T
        for _ in range(iterations):
            Q = P @ Rt.T + tt
            C, _ = closest(Q)
            mp, mc = P.mean(0), C.mean(0)
            H = (P - mp).T @ (C - mc)
            U, _, Vt = np.linalg.svd(H)
            D = np.diag([1, np.sign(np.linalg.det(Vt.T @ U.T))])
            Rt = Vt.T @ D @ U.T
            tt = mc - mp @ Rt.T
        _, dist = closest(P @ Rt.T + tt)
        rms = float(np.sqrt((dist ** 2).mean()))
        if best is None or rms < best[1]:
            best = (Rt, rms, float(dist.max()), tt)
    Rt, rms, mx, tt = best
    return (lambda xy: tuple((np.array(xy) @ Rt.T + tt).tolist())), rms, mx


def read_controls(path):
    """Control points: dicts with label, xy (feet), station (feet or None), offset (feet right, or None)."""
    out = []
    with open(path, newline="", encoding="utf8") as f:
        for r in csv.DictReader(f):
            m = re.match(r"\s*([\d.]+)\s*(left|right)?", r.get("offset_ft") or "")
            out.append({"label": r["label"], "xy": (float(r["easting_ft"]), float(r["northing_ft"])),
                        "station": _sta(r["station"]) if (r.get("station") or "").strip() else None,
                        "offset": float(m[1]) * (-1 if m[2] == "left" else 1) if m else None})
    return out


def place(points, controls, rotation, translation, along_weight=0.2):
    """Rigidly place traced `points` (x, y, station) on control points, starting from a rough
    placement (rotation radians, translation (x, y)). Across-track misses set the angle and
    are trusted; along-track misses are weighted down, since stationing over miles (and
    georeferencing) can stretch a little. Returns (rotation, translation, misses), where
    misses gives each used point's (along, across) miss in feet, across positive to the right."""
    import numpy as np
    from scipy.optimize import least_squares
    P = np.array([(p[0], p[1]) for p in points]); S = np.array([p[2] for p in points])
    seg = P[1:] - P[:-1]; seg_len = np.linalg.norm(seg, axis=1); keep = seg_len > 0

    def at(s):
        """Point and unit tangent at station s (local frame)."""
        i = min(max(int(np.searchsorted(S, s)), 1), len(P) - 1)
        u = (s - S[i - 1]) / ((S[i] - S[i - 1]) or 1); t = (P[i] - P[i - 1]) / (np.linalg.norm(P[i] - P[i - 1]) or 1)
        return P[i - 1] + u * (P[i] - P[i - 1]), t

    def nearest(q):
        """Signed distance (right +) from a local-frame point to the line."""
        a, d = P[:-1][keep], seg[keep]
        t = np.clip(((q - a) * d).sum(1) / (d ** 2).sum(1), 0, 1); c = a + t[:, None] * d
        j = np.linalg.norm(c - q, axis=1).argmin(); tv = d[j] / np.linalg.norm(d[j]); r = q - c[j]
        return tv[1] * r[0] - tv[0] * r[1]

    used = [c for c in controls if c["station"] is not None or c["offset"] is not None]

    def misses(v):
        th, tx, ty = v
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        out = []
        for c in used:
            q = (np.array(c["xy"]) - (tx, ty)) @ R       # control point in the local frame
            off = c["offset"] or 0.0
            if c["station"] is None:
                out.append((None, nearest(q) - off))
            else:
                p, t = at(c["station"]); p = p + off * np.array([t[1], -t[0]]); d = q - p
                out.append((d @ t, t[1] * d[0] - t[0] * d[1]))
        return out

    def resid(v):
        return [r for a, x in misses(v) for r in ([x] if a is None else [x, along_weight * a])]

    v = least_squares(resid, [rotation, *translation]).x
    return v[0], (v[1], v[2]), {c["label"]: m for c, m in zip(used, misses(v))}
