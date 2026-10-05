"""Railroad alignments from plat curve data (coordinate geometry).

An alignment is a sequence of elements, each running from one station to the
next: tangents (straight) and circular curves (radius, turning left or right).
Stations are in feet; a curve's length is its arc length, so a curve's radius
and central angle must agree with its stationing (checked on load).

The alignment is built in a local frame (feet, starting at the origin heading
east), then placed on the map by a rigid fit (rotation + translation, no
scaling) to reference geometry.

CSV columns: from_sta, to_sta, type (tangent | curve | equation), radius, delta, turn (L | R), sheet, notes
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
