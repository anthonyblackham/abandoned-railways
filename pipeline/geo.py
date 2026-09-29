"""Linear referencing along a traced track.

Measures are real metres along the line from its first vertex. Segment
lengths use the haversine formula; locating a point uses a flat projection
local to each segment, which is exact enough at the scale of a station.
"""

import math

EARTH_RADIUS = 6371008.8
METRES_PER_MILE = 1609.344


def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(h))


class Line:
    def __init__(self, coords):
        self.coords = [tuple(c[:2]) for c in coords]
        self.measures = [0.0]
        for a, b in zip(self.coords, self.coords[1:]):
            self.measures.append(self.measures[-1] + haversine(a, b))

    @property
    def length(self):
        return self.measures[-1]

    def locate(self, point):
        """Return (measure, offset): where `point` falls along the line and how far off it lies, in metres."""
        best = (math.inf, 0.0)
        for i, (a, b) in enumerate(zip(self.coords, self.coords[1:])):
            kx = math.radians(1) * EARTH_RADIUS * math.cos(math.radians((a[1] + b[1]) / 2))
            ky = math.radians(1) * EARTH_RADIUS
            dx, dy = (b[0] - a[0]) * kx, (b[1] - a[1]) * ky
            px, py = (point[0] - a[0]) * kx, (point[1] - a[1]) * ky
            seg2 = dx * dx + dy * dy
            u = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * dx + py * dy) / seg2))
            offset = math.hypot(px - u * dx, py - u * dy)
            if offset < best[0]:
                best = (offset, self.measures[i] + u * (self.measures[i + 1] - self.measures[i]))
        return best[1], best[0]

    def interpolate(self, measure):
        """Return the (lon, lat) at `measure` metres along the line."""
        measure = max(0.0, min(self.length, measure))
        for i in range(1, len(self.measures)):
            if self.measures[i] >= measure:
                span = self.measures[i] - self.measures[i - 1]
                u = 0.0 if span == 0 else (measure - self.measures[i - 1]) / span
                a, b = self.coords[i - 1], self.coords[i]
                return (a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]))
        return self.coords[-1]

    def slice(self, start, end):
        """Vertices from `start` to `end` metres, reversed when end < start."""
        lo, hi = sorted((start, end))
        pts = [self.interpolate(lo)]
        pts += [c for c, m in zip(self.coords, self.measures) if lo < m < hi]
        pts.append(self.interpolate(hi))
        return pts if start <= end else pts[::-1]
