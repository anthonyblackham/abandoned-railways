"""Read and write MapLibre glyph PBFs (the "glyphs.proto" format).

message glyph     { uint32 id = 1; bytes bitmap = 2; uint32 width = 3; uint32 height = 4;
                    sint32 left = 5; sint32 top = 6; uint32 advance = 7; }
message fontstack { string name = 1; string range = 2; repeated glyph glyphs = 3; }
message glyphs    { repeated fontstack stacks = 1; }

Small enough to hand-roll, so no protobuf dependency.
"""


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _zigzag(n):
    return (n << 1) ^ (n >> 63)


def _field(num, wire, payload):
    return _varint(num << 3 | wire) + payload


def _bytes(num, data):
    return _field(num, 2, _varint(len(data)) + data)


def encode(name, range_, glyphs):
    """glyphs: dicts with id, bitmap, width, height, left, top, advance."""
    stack = _bytes(1, name.encode()) + _bytes(2, range_.encode())
    for g in glyphs:
        body = _field(1, 0, _varint(g["id"]))
        if g["bitmap"]:
            body += _bytes(2, g["bitmap"])
        body += (_field(3, 0, _varint(g["width"])) + _field(4, 0, _varint(g["height"]))
                 + _field(5, 0, _varint(_zigzag(g["left"]))) + _field(6, 0, _varint(_zigzag(g["top"])))
                 + _field(7, 0, _varint(g["advance"])))
        stack += _bytes(3, body)
    return _bytes(1, stack)


def _read(buf):
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 2:
            n, i = _read_varint(buf, i)
            val, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"unexpected wire type {wire}")
        yield num, val


def _read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, i


def decode(data):
    """Return {codepoint: glyph dict} for the first fontstack."""
    out = {}
    for num, stack in _read(data):
        for snum, sval in _read(stack):
            if snum == 3:
                g = {}
                for gnum, gval in _read(sval):
                    key = {1: "id", 2: "bitmap", 3: "width", 4: "height", 5: "left", 6: "top", 7: "advance"}[gnum]
                    if key in ("left", "top"):
                        gval = (gval >> 1) ^ -(gval & 1)
                    g[key] = gval
                out[g["id"]] = g
    return out
