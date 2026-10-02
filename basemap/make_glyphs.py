"""Build MapLibre glyph PBFs from the TTF fonts in basemap/fonts/.

MapLibre draws text from signed-distance-field glyphs, served as
web/fonts/<fontstack>/<start>-<end>.pbf in ranges of 256 code points.
This renders each glyph at 4x with FreeType, computes the distance field
at that resolution and averages it down, matching the layout of the
standard glyph tools (24px glyphs, 3px border, radius 8, edge at 191),
which was checked against OpenFreeMap's own Noto Sans glyphs.

    pip install freetype-py numpy scipy
    python basemap/make_glyphs.py
"""

import math
from pathlib import Path

import freetype
import numpy as np
from scipy.ndimage import distance_transform_edt

from glyph_pbf import encode

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "web" / "fonts"

FONTS = {
    "Old Standard TT Regular": "OldStandard-Regular.ttf",
    "Old Standard TT Italic": "OldStandard-Italic.ttf",
    "Old Standard TT Bold": "OldStandard-Bold.ttf",
}

SIZE = 24          # glyph size MapLibre expects
BUFFER = 3         # border around each glyph bitmap
RADIUS = 8         # distance (px) covered by the 0-255 range
CUTOFF = 0.25      # puts the glyph edge at 255 * 0.75 = 191
TOP_OFFSET = 26    # MapLibre's baseline convention: top = glyph top - 26
SCALE = 4          # render at 4x, then average the distance field down


def render(face, codepoint):
    face.load_char(chr(codepoint), freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING)
    g = face.glyph
    advance = round(g.advance.x / 64 / SCALE)
    bm = g.bitmap
    if bm.width == 0 or bm.rows == 0:
        return {"id": codepoint, "bitmap": b"", "width": 0, "height": 0,
                "left": 0, "top": -TOP_OFFSET, "advance": advance}
    hi = np.array(bm.buffer, dtype=np.uint8).reshape(bm.rows, bm.pitch)[:, :bm.width]

    # Align the 4x bitmap to the 1x pixel grid.
    left = math.floor(g.bitmap_left / SCALE)
    top = math.ceil(g.bitmap_top / SCALE)
    width = math.ceil((g.bitmap_left + bm.width - left * SCALE) / SCALE)
    height = math.ceil((top * SCALE - (g.bitmap_top - bm.rows)) / SCALE)
    W, H = (width + 2 * BUFFER) * SCALE, (height + 2 * BUFFER) * SCALE
    canvas = np.zeros((H, W), dtype=np.uint8)
    x0 = g.bitmap_left - left * SCALE + BUFFER * SCALE
    y0 = top * SCALE - g.bitmap_top + BUFFER * SCALE
    canvas[y0:y0 + bm.rows, x0:x0 + bm.width] = hi

    inside = canvas >= 128
    # Signed distance in 4x pixels: positive outside the glyph, negative inside.
    d = distance_transform_edt(~inside) - distance_transform_edt(inside)
    d = d.reshape(H // SCALE, SCALE, W // SCALE, SCALE).mean(axis=(1, 3)) / SCALE
    values = np.clip(np.round(255 - 255 * (d / RADIUS + CUTOFF)), 0, 255).astype(np.uint8)
    return {"id": codepoint, "bitmap": values.tobytes(), "width": width, "height": height,
            "left": left, "top": top - TOP_OFFSET, "advance": advance}


def build(stack, filename):
    face = freetype.Face(str(HERE / "fonts" / filename))
    face.set_pixel_sizes(0, SIZE * SCALE)
    codepoints = sorted(cp for cp, _ in face.get_chars() if cp <= 0xFFFF)
    folder = OUT / stack
    folder.mkdir(parents=True, exist_ok=True)
    ranges = {}
    for cp in codepoints:
        ranges.setdefault(cp // 256, []).append(cp)
    for r, cps in ranges.items():
        start, end = r * 256, r * 256 + 255
        glyphs = [render(face, cp) for cp in cps]
        (folder / f"{start}-{end}.pbf").write_bytes(encode(stack, f"{start}-{end}", glyphs))
    print(f"{stack}: {len(codepoints)} glyphs in {len(ranges)} ranges")


if __name__ == "__main__":
    for stack, filename in FONTS.items():
        build(stack, filename)
