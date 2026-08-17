#!/usr/bin/env python3
"""Derive a dark-background version of the logo from the existing PNG.

The wordmark is chrome (light) but "AUTO RECYCLING" underneath is solid black,
so on the graphite footer that half of the logo disappears. This lifts only the
near-black, low-saturation pixels to off-white and leaves the chrome gradient and
the green recycle mark untouched.

It is a stopgap, not a substitute for the real vector art — but it makes the
footer and the coming-soon page correct today.

    python3 scripts/make_light_logo.py brand/ab-motors-logo-legacy.png
"""
from __future__ import annotations

import pathlib
import struct
import sys
import zlib

# Anything darker than this and this desaturated is treated as the black text.
DARK_MAX = 110
SATURATION_MAX = 30
TARGET = (242, 245, 246)  # --chrome-3, matches the theme's light-on-dark text
# Fraction of the image height where the wordmark ends and the tagline begins.
BAND_TOP = 0.70


def read_png(path: pathlib.Path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"{path} is not a PNG")
    pos, idat = 8, b""
    width = height = depth = ctype = None
    plte = trns = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if interlace:
                sys.exit("interlaced PNG not supported")
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"PLTE":
            plte = chunk
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IEND":
            break
    if depth != 8:
        sys.exit(f"only 8-bit PNGs supported (this one is {depth}-bit)")

    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = channels
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        out += line
        prev = line
    return width, height, ctype, channels, bytes(out), plte, trns


def to_rgba(width, height, ctype, channels, data, plte, trns):
    px = bytearray(width * height * 4)
    for idx in range(width * height):
        src = data[idx * channels:(idx + 1) * channels]
        if ctype == 6:
            r, g, b, a = src
        elif ctype == 2:
            r, g, b = src
            a = 255
        elif ctype == 0:
            r = g = b = src[0]
            a = 255
        elif ctype == 4:
            r = g = b = src[0]
            a = src[1]
        elif ctype == 3:
            i = src[0]
            r, g, b = plte[i * 3:i * 3 + 3]
            a = trns[i] if trns and i < len(trns) else 255
        px[idx * 4:idx * 4 + 4] = bytes((r, g, b, a))
    return px


def write_png(path: pathlib.Path, width: int, height: int, px: bytes) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw += px[y * width * 4:(y + 1) * width * 4]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "brand/ab-motors-logo-legacy.png")
    out = src.parent / "ab-motors-logo-light.png"

    w, h, ctype, ch, data, plte, trns = read_png(src)
    px = to_rgba(w, h, ctype, ch, data, plte, trns)

    # Restrict the lift to the tagline band. A whole-image threshold also caught
    # the dark bevel inside the chrome wordmark, which flattened the "&" and the
    # "B" into solid white slabs and dulled the arrows in the recycle mark.
    # Only "AUTO RECYCLING" lives below this line, so nothing else can be hit.
    band_start = int(h * BAND_TOP)

    lifted = 0
    for y in range(band_start, h):
        for x in range(w):
            i = (y * w + x) * 4
            r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
            if a < 8:
                continue
            if max(r, g, b) <= DARK_MAX and (max(r, g, b) - min(r, g, b)) <= SATURATION_MAX:
                px[i], px[i + 1], px[i + 2] = TARGET
                lifted += 1

    # The arrows inside the recycle mark are transparent holes, not white paint.
    # On a white page they read as white; on the graphite footer they turn dark and
    # the mark stops looking like a recycle symbol. Fill the holes inside the green
    # disc with white so it survives either background.
    green = [(x, y) for y in range(band_start) for x in range(w)
             if px[(y * w + x) * 4 + 3] > 128
             and px[(y * w + x) * 4 + 1] > px[(y * w + x) * 4] + 25
             and px[(y * w + x) * 4 + 1] > px[(y * w + x) * 4 + 2] + 25]
    filled = 0
    if green:
        xs = [p[0] for p in green]
        ys = [p[1] for p in green]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

        # A bounding-box fill also whitens the transparent corners *outside* the
        # disc, leaving a white square behind it. So flood-fill inward from the box
        # border through transparent pixels: whatever the flood cannot reach is an
        # enclosed hole (the arrows), and only those get painted.
        import collections as _c
        outside = bytearray((x1 - x0 + 1) * (y1 - y0 + 1))
        bw = x1 - x0 + 1

        def clear(x, y):
            return px[(y * w + x) * 4 + 3] < 128

        queue = _c.deque()
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                if clear(x, y):
                    queue.append((x, y))
        for y in range(y0, y1 + 1):
            for x in (x0, x1):
                if clear(x, y):
                    queue.append((x, y))
        while queue:
            x, y = queue.popleft()
            k = (y - y0) * bw + (x - x0)
            if outside[k]:
                continue
            outside[k] = 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if x0 <= nx <= x1 and y0 <= ny <= y1 and clear(nx, ny) and not outside[(ny - y0) * bw + (nx - x0)]:
                    queue.append((nx, ny))

        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                i = (y * w + x) * 4
                if px[i + 3] < 128 and not outside[(y - y0) * bw + (x - x0)]:
                    px[i], px[i + 1], px[i + 2], px[i + 3] = 255, 255, 255, 255
                    filled += 1
        print(f"  recycle mark found at x {x0}-{x1}, y {y0}-{y1}")

    write_png(out, w, h, bytes(px))
    print(f"{src.name}  {w}x{h}")
    print(f"  lifted {lifted} dark pixels in the tagline band (y >= {band_start}) to #F2F5F6")
    print(f"  filled {filled} transparent pixels inside the recycle mark with white")
    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print("\nUpload it in the theme editor under Brand -> \"Logo for dark backgrounds\".")


if __name__ == "__main__":
    main()
