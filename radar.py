"""Radar-lite for florina-weather. Standard library only (Python 3.10+).

RainViewer's free tier still serves radar tiles (past frames, zoom <= 7).
Shipping those PNGs to every visitor costs ~100 KB per loop. Instead the
server fetches the few tiles it needs ONCE per refresh, decodes them, and
republishes a tiny palette-indexed grid:

    dry hour    ~0.1 KB      rainy hour   a few KB (less after gzip)

radar.js paints that grid on a <canvas>. Visitors never touch RainViewer.

Wiring (app.py):
    import radar
    RADAR = radar.Radar(lat=40.7819, lon=21.4090)   # your PLACE coordinates
    RADAR.start()                                   # background refresh thread
    # GET /api/radar -> RADAR.payload() (bytes, JSON) or 503 while it is None

Pass fetch=<your cacert-aware bytes fetcher> to reuse your own HTTP helper;
`_fetch` already prefers the project's trust store when sources.py is
importable, because a bare urlopen has no certificates on Wasmer Edge.

Attribution is required by RainViewer's terms: radar.js's panel carries it.
"""
from __future__ import annotations

import json
import math
import struct
import threading
import time
import urllib.request
import zlib
from collections import Counter

INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"
ZOOM = 7            # free-tier maximum since 2026-01-01
COLOR = 2           # "Universal Blue", the only scheme left on the free tier
OPTIONS = "0_0"     # no smoothing (keeps exact palette colours), no snow tint
GRID = 64           # output cells per side
FRAMES = 6          # 10-minute steps -> the last hour
RADIUS_KM = 90
PALETTE_MAX = 15    # index 0 is "no echo"; 4 bits per cell would still fit


def _fetch(url, timeout=15):
    """Fetch bytes using the project's trust store where there is one.

    A bare urlopen is not enough: Wasmer Edge loads zero certificates into
    Python's default context, so every HTTPS call there fails with
    CERTIFICATE_VERIFY_FAILED — and a radar that cannot fetch simply never
    appears, with nothing on the page to say why. sources.ssl_context knows
    where the bundled cacert.pem is. The import is guarded so the module still
    works standalone.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": "florina-weather/1.0"})
    context = None
    try:
        import sources
        context = sources.ssl_context()
    except Exception:  # noqa: BLE001 - standalone use, accept the default
        context = None
    with urllib.request.urlopen(request, timeout=timeout,
                                context=context) as response:
        return response.read(2_000_000)


# --------------------------------------------------------------------------
# Minimal PNG reader: 8-bit RGB/RGBA and 1-8 bit palette, non-interlaced.
# --------------------------------------------------------------------------
class Png:
    def __init__(self, data):
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG")
        pos, idat = 8, []
        self.plte = self.trns = b""
        # Declared up front so a truncated file raises a readable error instead
        # of an AttributeError from further down.
        self.w = self.h = self.depth = self.ctype = None
        inter = 0
        while pos + 8 <= len(data):
            size, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + size]
            pos += 12 + size
            if kind == b"IHDR":
                self.w, self.h, self.depth, self.ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            elif kind == b"PLTE":
                self.plte = body
            elif kind == b"tRNS":
                self.trns = body
            elif kind == b"IDAT":
                idat.append(body)
            elif kind == b"IEND":
                break
        if not idat:
            raise ValueError("no image data")
        if self.ctype is None or self.w is None:
            raise ValueError("no IHDR")
        channels = {2: 3, 3: 1, 6: 4}.get(self.ctype)
        if inter or channels is None or (self.depth != 8 and self.ctype != 3):
            raise ValueError("unsupported PNG variant")
        bits = channels * self.depth
        stride, bpp = (self.w * bits + 7) // 8, max(1, bits // 8)
        raw = zlib.decompress(b"".join(idat))
        # A transparent tile is all zero bytes: skip the slow unfilter loop.
        self.blank = not raw.strip(b"\0") and (
            self.ctype == 6 or (self.ctype == 3 and self.trns[:1] == b"\0"))
        self.rows = [] if self.blank else self._unfilter(raw, stride, bpp)

    def _unfilter(self, raw, stride, bpp):
        rows, prev, i = [], bytearray(stride), 0
        for _ in range(self.h):
            kind = raw[i]
            cur = bytearray(raw[i + 1:i + 1 + stride])
            i += 1 + stride
            if kind == 1:
                for x in range(bpp, stride):
                    cur[x] = (cur[x] + cur[x - bpp]) & 255
            elif kind == 2:
                for x in range(stride):
                    cur[x] = (cur[x] + prev[x]) & 255
            elif kind == 3:
                for x in range(stride):
                    a = cur[x - bpp] if x >= bpp else 0
                    cur[x] = (cur[x] + ((a + prev[x]) >> 1)) & 255
            elif kind == 4:
                for x in range(stride):
                    a = cur[x - bpp] if x >= bpp else 0
                    b = prev[x]
                    c = prev[x - bpp] if x >= bpp else 0
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - c - c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    cur[x] = (cur[x] + pr) & 255
            elif kind != 0:
                raise ValueError("bad PNG filter")
            rows.append(cur)
            prev = cur
        return rows

    def pixel(self, x, y):
        """(r, g, b, a) at x, y."""
        row = self.rows[y]
        if self.ctype == 6:
            i = x * 4
            return row[i], row[i + 1], row[i + 2], row[i + 3]
        if self.ctype == 2:
            i = x * 3
            return row[i], row[i + 1], row[i + 2], 255
        d = self.depth
        idx = (row[x * d // 8] >> (8 - d - (x * d) % 8)) & ((1 << d) - 1)
        r, g, b = self.plte[idx * 3:idx * 3 + 3]
        return r, g, b, (self.trns[idx] if idx < len(self.trns) else 255)


# --------------------------------------------------------------------------
# Palette, projection, grid sampling
# --------------------------------------------------------------------------
class Palette:
    """Stable colour -> small integer map shared by every frame (0 = no echo)."""

    def __init__(self):
        self.colors = []  # (r, g, b, a)

    def index(self, color):
        if color in self.colors:
            return self.colors.index(color) + 1
        if len(self.colors) < PALETTE_MAX:
            self.colors.append(color)
            return len(self.colors)
        best = min(range(len(self.colors)),
                   key=lambda i: sum((p - q) ** 2 for p, q in zip(self.colors[i], color)))
        return best + 1

    def css(self):
        return ["rgba(%d,%d,%d,%.2f)" % (r, g, b, a / 255) for r, g, b, a in self.colors]


def world_px(lat, lon, zoom):
    """Web-Mercator pixel coordinates (256 px tiles)."""
    n = 256 * 2 ** zoom
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def rle(grid):
    """Flat [value, run, value, run, ...]; a dry frame is just [0, 4096]."""
    out, prev, run = [], grid[0], 0
    for v in grid:
        if v == prev:
            run += 1
        else:
            out += [prev, run]
            prev, run = v, 1
    return out + [prev, run]


def sample_grid(png_for, cx, cy, half, n, palette):
    """n x n cells covering the square centred on world pixel (cx, cy).

    Each cell takes the most common echo colour among 9 samples, needing at
    least 3 of them, so isolated speckle is ignored.
    """
    step = 2 * half / n
    sub = step / 3
    out = bytearray(n * n)
    for j in range(n):
        for i in range(n):
            gx = cx - half + (i + .5) * step
            gy = cy - half + (j + .5) * step
            votes = Counter()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    px, py = int(gx + dx * sub), int(gy + dy * sub)
                    png = png_for(px >> 8, py >> 8)
                    if png is None or png.blank:
                        continue
                    r, g, b, a = png.pixel(px & 255, py & 255)
                    # Only fully opaque pixels are echo. RainViewer draws a
                    # semi-transparent tan coverage mask into the same tile,
                    # marking where radar coverage exists, and it is common
                    # enough to win the vote wherever it appears — sampling it
                    # painted the card tan and buried the actual rain. Every
                    # real echo colour is opaque; every mask colour is not.
                    if a == 255:
                        votes[(r, g, b, a)] += 1
            if sum(votes.values()) >= 3:
                out[j * n + i] = palette.index(votes.most_common(1)[0][0])
    return out


# --------------------------------------------------------------------------
class Radar:
    def __init__(self, lat, lon, radius_km=RADIUS_KM, fetch=_fetch, interval=600):
        self.lat, self.lon, self.radius_km = lat, lon, radius_km
        self.fetch, self.interval = fetch, interval
        self.cx, self.cy = world_px(lat, lon, ZOOM)
        mpp = 156543.03392 * math.cos(math.radians(lat)) / 2 ** ZOOM  # metres per px
        self.half = radius_km * 1000 / mpp
        self.palette = Palette()
        self.error = None
        self._frames = {}        # frame time -> grid (bytearray)
        self._payload = None     # bytes, replaced atomically

    def payload(self):
        return self._payload

    def start(self):
        def loop():
            while True:
                try:
                    self.refresh()
                    self.error = None
                except Exception as exc:  # keep serving the last good payload
                    self.error = repr(exc)[:200]
                time.sleep(self.interval)
        threading.Thread(target=loop, name="radar", daemon=True).start()

    def refresh(self):
        index = json.loads(self.fetch(INDEX_URL))
        host, past = index["host"], index["radar"]["past"][-FRAMES:]
        for frame in past:
            if frame["time"] not in self._frames:
                self._frames[frame["time"]] = self._build_frame(host, frame["path"])
        keep = {f["time"] for f in past}
        for t in list(self._frames):
            if t not in keep:
                del self._frames[t]
        times = sorted(self._frames)
        self._payload = json.dumps({
            "generated": int(time.time()),
            "t": times,
            "n": GRID,
            "km": self.radius_km,
            "half": self.half / (256 * 2 ** ZOOM),   # half-width in mercator units
            "center": [self.lat, self.lon],
            "palette": self.palette.css(),
            "frames": [rle(self._frames[t]) for t in times],
        }, separators=(",", ":")).encode()

    def _build_frame(self, host, path):
        x0, x1 = int(self.cx - self.half) >> 8, int(self.cx + self.half) >> 8
        y0, y1 = int(self.cy - self.half) >> 8, int(self.cy + self.half) >> 8
        tiles = {}
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                url = "%s%s/256/%d/%d/%d/%d/%s.png" % (host, path, ZOOM, tx, ty, COLOR, OPTIONS)
                tiles[(tx, ty)] = Png(self.fetch(url))
        if all(t.blank for t in tiles.values()):
            return bytearray(GRID * GRID)
        return sample_grid(lambda tx, ty: tiles.get((tx, ty)),
                           self.cx, self.cy, self.half, GRID, self.palette)
