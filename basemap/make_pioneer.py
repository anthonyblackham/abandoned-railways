"""Generate pioneer.json and its paper-texture sprite.

A flat, near-monochrome railroad-map style after Thunderforest's Pioneer:
parchment land, pale grey-lavender water, faint grey roads with no casings,
dark grey railways with cross-ties, darker-parchment buildings close up, black
labels with place markers. Colours were sampled from Pioneer's own sample
image. Edit the constants here (or the JSON in Maputnik) and rerun:

    python basemap/make_pioneer.py
"""

import json
import random
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent.parent / "web" / "styles"

PAPER = "#f0deb8"
INK = "#1e1a16"
WATER = "#d5d5da"
WATERLINE = "#b9b9c2"
ROAD = "#cdcdd1"
ROAD_MAJOR = "#bdbdc3"
RAIL = "#6c6a68"
BUILDING = "#e0c99b"   # a darker parchment, so buildings recede
STATE = "#a8322a"      # state names, red as on Pioneer


def paper_texture(size):
    """A faint, tileable parchment mottle."""
    random.seed(7)
    im = Image.new("L", (size, size), 128)
    px = im.load()
    for _ in range(int(140 * (size / 256) ** 2)):
        cx, cy = random.uniform(0, size), random.uniform(0, size)
        r = random.uniform(6, 28) * size / 256
        d = random.choice((-1, 1)) * random.uniform(3, 9)
        for x in range(int(cx - r), int(cx + r) + 1):
            for y in range(int(cy - r), int(cy + r) + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    xx, yy = x % size, y % size
                    px[xx, yy] = int(max(0, min(255, px[xx, yy] + d)))
    im = im.filter(ImageFilter.GaussianBlur(size / 64))
    base = tuple(int(PAPER[i:i + 2], 16) for i in (1, 3, 5))
    out = Image.new("RGBA", (size, size))
    op = out.load()
    for x in range(size):
        for y in range(size):
            v = (px[x, y] - 128) / 128 * 13
            op[x, y] = tuple(max(0, min(255, int(c + v))) for c in base) + (255,)
    return out


def write_sprite():
    for scale, suffix in ((1, ""), (2, "@2x")):
        im = paper_texture(256 * scale)
        im.save(HERE / f"sprite{suffix}.png")
        (HERE / f"sprite{suffix}.json").write_text(json.dumps(
            {"paper": {"x": 0, "y": 0, "width": im.width, "height": im.height, "pixelRatio": scale}}))


# Prefer the Latin-script name, so every label can be drawn in the period lettering.
NAME = ["coalesce", ["get", "name:latin"], ["get", "name"]]


def Z(*stops):
    return ["interpolate", ["exponential", 1.4], ["zoom"], *stops]


def cls(*c):
    return ["match", ["get", "class"], list(c), True, False]


def style():
    font, bold, italic = ["Old Standard TT Regular"], ["Old Standard TT Bold"], ["Old Standard TT Italic"]
    halo = {"text-halo-color": PAPER, "text-halo-width": 1.4}
    surface = ["!=", ["get", "brunnel"], "tunnel"]
    main = ["!", ["has", "service"]]
    src = {"source": "openmaptiles"}
    layers = [
        {"id": "background", "type": "background", "paint": {"background-color": PAPER}},
        {"id": "paper", "type": "background", "paint": {"background-pattern": "paper"}},
        {"id": "water", "type": "fill", **src, "source-layer": "water", "filter": surface,
         "paint": {"fill-color": WATER, "fill-outline-color": WATERLINE}},
        {"id": "river", "type": "line", **src, "source-layer": "waterway", "filter": ["all", surface, cls("river")],
         "paint": {"line-color": WATERLINE, "line-width": Z(8, 0.6, 16, 3)}},
        {"id": "stream", "type": "line", **src, "source-layer": "waterway", "minzoom": 13,
         "filter": ["all", surface, ["!", cls("river")]],
         "paint": {"line-color": WATERLINE, "line-width": Z(13, 0.4, 16, 1.2)}},
        {"id": "building", "type": "fill", **src, "source-layer": "building", "minzoom": 14,
         "paint": {"fill-color": BUILDING, "fill-outline-color": BUILDING, "fill-opacity": 0.9}},
        # Roads: faint grey, no casings.
        {"id": "road_minor", "type": "line", **src, "source-layer": "transportation", "minzoom": 12,
         "filter": ["all", surface, cls("minor", "service")], "layout": {"line-cap": "round", "line-join": "round"},
         "paint": {"line-color": ROAD, "line-width": Z(12, 0.5, 18, 6)}},
        {"id": "road_secondary", "type": "line", **src, "source-layer": "transportation", "minzoom": 9,
         "filter": ["all", surface, cls("secondary", "tertiary")], "layout": {"line-cap": "round", "line-join": "round"},
         "paint": {"line-color": ROAD, "line-width": Z(9, 0.6, 18, 8)}},
        {"id": "road_primary", "type": "line", **src, "source-layer": "transportation", "minzoom": 8,
         "filter": ["all", surface, cls("primary")], "layout": {"line-cap": "round", "line-join": "round"},
         "paint": {"line-color": ROAD_MAJOR, "line-width": Z(8, 0.6, 18, 10)}},
        {"id": "road_major", "type": "line", **src, "source-layer": "transportation", "minzoom": 5,
         "filter": ["all", surface, cls("motorway", "trunk")],
         "layout": {"line-cap": "round", "line-join": "round"},
         "paint": {"line-color": ROAD_MAJOR, "line-width": Z(5, 0.6, 18, 10)}},
        {"id": "boundary_state", "type": "line", **src, "source-layer": "boundary",
         "filter": ["all", ["match", ["get", "admin_level"], [2, 4], True, False], ["!=", ["get", "maritime"], 1]],
         "paint": {"line-color": "#a8957a", "line-width": Z(3, 0.6, 10, 1.4), "line-dasharray": [3, 2, 1, 2]}},
        # Railways: the one strong line work.
        {"id": "rail_yard", "type": "line", **src, "source-layer": "transportation", "minzoom": 13,
         "filter": ["all", cls("rail"), ["has", "service"]],
         "paint": {"line-color": RAIL, "line-width": Z(13, 0.5, 18, 1.5)}},
        {"id": "rail_tunnel", "type": "line", **src, "source-layer": "transportation", "minzoom": 7,
         "filter": ["all", cls("rail"), main, ["==", ["get", "brunnel"], "tunnel"]],
         "paint": {"line-color": RAIL, "line-width": Z(7, 0.6, 16, 2), "line-dasharray": [3, 2], "line-opacity": 0.6}},
        {"id": "transit", "type": "line", **src, "source-layer": "transportation", "minzoom": 11,
         "filter": ["all", cls("transit"), main, surface],
         "paint": {"line-color": "#8a8784", "line-width": Z(11, 0.5, 16, 1.4)}},
        {"id": "rail_ties", "type": "line", **src, "source-layer": "transportation", "minzoom": 9,
         "filter": ["all", cls("rail"), main, surface],
         "paint": {"line-color": RAIL, "line-width": Z(9, 4, 13, 7, 18, 12), "line-dasharray": [0.2, 1.8]}},
        {"id": "rail", "type": "line", **src, "source-layer": "transportation", "minzoom": 5,
         "filter": ["all", cls("rail"), main, surface], "layout": {"line-join": "round"},
         "paint": {"line-color": RAIL, "line-width": Z(5, 0.6, 16, 2.2)}},
        # Labels: black, with place markers like the old railroad maps.
        {"id": "water_label", "type": "symbol", **src, "source-layer": "water_name",
         "filter": ["match", ["geometry-type"], ["Point", "MultiPoint"], True, False],
         "layout": {"text-field": NAME, "text-font": italic, "text-size": 12},
         "paint": {"text-color": "#55555f", **halo}},
        {"id": "river_label", "type": "symbol", **src, "source-layer": "waterway", "minzoom": 10,
         "filter": cls("river"),
         "layout": {"text-field": NAME, "text-font": italic, "text-size": 11.5, "symbol-placement": "line"},
         "paint": {"text-color": "#55555f", **halo}},
        {"id": "place_marker", "type": "circle", **src, "source-layer": "place", "minzoom": 6, "maxzoom": 13,
         "filter": cls("city", "town", "village"),
         "paint": {"circle-radius": ["match", ["get", "class"], "city", 3.2, "town", 2.6, 2],
                   "circle-color": ["match", ["get", "class"], "city", INK, PAPER],
                   "circle-stroke-color": INK, "circle-stroke-width": ["match", ["get", "class"], "city", 0, 1]}},
    ]
    layers.append({"id": "place_state", "type": "symbol", **src, "source-layer": "place", "minzoom": 3, "maxzoom": 8,
                   "filter": cls("state"),
                   "layout": {"text-field": NAME, "text-font": font, "text-size": Z(3, 11, 7, 17),
                              "text-transform": "uppercase", "text-letter-spacing": 0.25, "text-max-width": 12},
                   "paint": {"text-color": STATE, **halo}})
    for pid, classes, f, size, mz in [("village", ["village", "hamlet"], font, Z(10, 10.5, 16, 14), 10),
                                      ("town", ["town"], font, Z(8, 12, 16, 18), 8),
                                      ("city", ["city"], bold, Z(5, 13, 14, 24), 4)]:
        layers.append({"id": f"place_{pid}", "type": "symbol", **src, "source-layer": "place", "minzoom": mz,
                       "filter": cls(*classes),
                       "layout": {"text-field": NAME, "text-font": f, "text-size": size,
                                  "text-anchor": "bottom", "text-offset": [0, -0.4]},
                       # A wider halo, so railways passing under a name don't show between letters.
                       "paint": {"text-color": INK, "text-halo-color": PAPER, "text-halo-width": 2.4}})
    return {
        "version": 8, "name": "Abandoned Railways: Pioneer", "metadata": {"maputnik:renderer": "mlgljs"},
        "center": [-122.6, 45.3], "zoom": 10, "sprite": "styles/sprite",
        "sources": {"openmaptiles": {"type": "vector", "url": "https://tiles.openfreemap.org/planet"}},
        "glyphs": "fonts/{fontstack}/{range}.pbf",   # built by basemap/make_glyphs.py
        "layers": layers,
    }


if __name__ == "__main__":
    write_sprite()
    s = style()
    (HERE / "pioneer.json").write_text(json.dumps(s, indent=1), encoding="utf8")
    print(len(s["layers"]), "layers")
