# -*- coding: utf-8 -*-
"""Tests for the radar: the PNG decoder, the grid sampling, and the wire shape.

The decoder is the part worth pinning down. It reads real RainViewer tiles
with nothing but zlib, so a mistake in an unfilter loop would not crash — it
would quietly place rain in the wrong cells.
"""

import json
import os
import re
import struct
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import radar  # noqa: E402


def _chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def _filter_row(kind, row, prev, bpp):
    """Apply a PNG filter to one row, the way an encoder would."""
    out = bytearray(len(row))
    for i, value in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i] if prev else 0
        c = prev[i - bpp] if (prev and i >= bpp) else 0
        if kind == 0:
            predict = 0
        elif kind == 1:
            predict = a
        elif kind == 2:
            predict = b
        elif kind == 3:
            predict = (a + b) >> 1
        else:
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - c - c)
            predict = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out[i] = (value - predict) & 0xFF
    return bytes(out)


def make_png(width, height, pixels, ctype=2, depth=8, filter_type=0, palette=None):
    """Build a PNG the decoder should read back exactly.

    ``pixels`` is a flat row-major list of RGB(A) tuples, or of palette indexes
    when ``ctype`` is 3.
    """
    if ctype == 6:
        raw_px = [byte for px in pixels for byte in px[:4]]
    elif ctype == 2:
        raw_px = [byte for px in pixels for byte in px[:3]]
    else:
        raw_px = list(pixels)

    if ctype == 3:
        stride = (width * depth + 7) // 8
        rows = []
        for y in range(height):
            packed = bytearray(stride)
            for x in range(width):
                idx = raw_px[y * width + x]
                packed[x * depth // 8] |= (idx & 0xFF) << (8 - depth - (x * depth) % 8)
            rows.append(bytes(packed))
    else:
        channels = 4 if ctype == 6 else 3
        stride = width * channels
        rows = [bytes(raw_px[y * stride:(y + 1) * stride]) for y in range(height)]

    bpp = max(1, ((4 if ctype == 6 else 3) * depth) // 8) if ctype != 3 else 1
    body = bytearray()
    prev = None
    for row in rows:
        body.append(filter_type)
        body += _filter_row(filter_type, row, prev, bpp)
        prev = row

    ihdr = struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    if palette:
        out += _chunk(b"PLTE", bytes(palette))
    return out + _chunk(b"IDAT", zlib.compress(bytes(body))) + _chunk(b"IEND", b"")


class DecoderTests(unittest.TestCase):
    """Every colour type, under every filter, because a filter bug is silent."""

    FILTERS = (0, 1, 2, 3, 4)

    def test_rgba_under_every_filter(self):
        pixels = [(10, 20, 30, 255), (40, 50, 60, 200),
                  (70, 80, 90, 128), (100, 110, 120, 0)]
        for kind in self.FILTERS:
            png = radar.Png(make_png(2, 2, pixels, ctype=6, filter_type=kind))
            for index, (r, g, b, a) in enumerate(pixels):
                self.assertEqual(png.pixel(index % 2, index // 2), (r, g, b, a),
                                 "filter %d, pixel %d" % (kind, index))

    def test_rgb_under_every_filter(self):
        pixels = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (250, 251, 252)]
        for kind in self.FILTERS:
            png = radar.Png(make_png(2, 2, pixels, ctype=2, filter_type=kind))
            for index, (r, g, b) in enumerate(pixels):
                self.assertEqual(png.pixel(index % 2, index // 2), (r, g, b, 255),
                                 "filter %d, pixel %d" % (kind, index))

    def test_palette_under_every_filter(self):
        palette = [255, 0, 0, 0, 255, 0, 0, 0, 255]
        indexes = [0, 1, 2, 1]
        for kind in self.FILTERS:
            png = radar.Png(make_png(2, 2, indexes, ctype=3, depth=8,
                                     filter_type=kind, palette=palette))
            expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 0)]
            for index, rgb in enumerate(expected):
                self.assertEqual(png.pixel(index % 2, index // 2), rgb + (255,),
                                 "filter %d, pixel %d" % (kind, index))

    def test_a_low_bit_depth_palette_reads_back(self):
        # 2-bit indexes pack four to a byte, which is where the bit maths can
        # go wrong without raising.
        palette = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0]
        png = radar.Png(make_png(4, 1, [0, 1, 2, 3], ctype=3, depth=2,
                                 palette=palette))
        self.assertEqual([png.pixel(x, 0)[:3] for x in range(4)],
                         [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)])

    def test_a_transparent_tile_is_recognised_as_blank(self):
        pixels = [(0, 0, 0, 0)] * 4
        png = radar.Png(make_png(2, 2, pixels, ctype=6))
        self.assertTrue(png.blank)
        self.assertEqual(png.rows, [])

    def test_a_tile_with_any_echo_is_not_blank(self):
        pixels = [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (9, 9, 9, 255)]
        png = radar.Png(make_png(2, 2, pixels, ctype=6))
        self.assertFalse(png.blank)

    def test_rubbish_is_rejected_rather_than_half_read(self):
        with self.assertRaises(ValueError):
            radar.Png(b"not a png at all")
        with self.assertRaises(ValueError):
            radar.Png(b"\x89PNG\r\n\x1a\n" + b"garbage")


class HelpersTests(unittest.TestCase):
    def test_rle_round_trips(self):
        for grid in ([0] * 16, [0, 0, 1, 1, 1, 2], list(range(8))):
            runs = radar.rle(grid)
            out, at = [], 0
            for i in range(0, len(runs), 2):
                out += [runs[i]] * runs[i + 1]
                at += runs[i + 1]
            self.assertEqual(out, grid)
            self.assertEqual(at, len(grid))

    def test_a_dry_frame_is_one_run(self):
        self.assertEqual(radar.rle(bytearray(radar.GRID ** 2)),
                         [0, radar.GRID ** 2])

    def test_florina_projects_into_the_tile_it_should(self):
        x, y = radar.world_px(40.7819, 21.4090, radar.ZOOM)
        self.assertEqual((int(x) >> 8, int(y) >> 8), (71, 48))

    def test_the_palette_is_stable_and_reuses_colours(self):
        palette = radar.Palette()
        first = palette.index((10, 20, 30, 255))
        self.assertEqual(palette.index((10, 20, 30, 255)), first)
        self.assertEqual(len(palette.colors), 1)

    def test_the_palette_maps_an_overflow_colour_to_a_neighbour(self):
        palette = radar.Palette()
        for i in range(radar.PALETTE_MAX):
            palette.index((i * 4, 0, 0, 255))
        # A sixteenth colour must become an index, not a crash or a new entry.
        index = palette.index((1, 0, 0, 255))
        self.assertEqual(len(palette.colors), radar.PALETTE_MAX)
        self.assertGreaterEqual(index, 1)


class SamplingTests(unittest.TestCase):
    """A storm must land where it actually is - not mirrored, not rotated.

    sample_grid works in absolute world pixels, so the grid is placed inside a
    single synthetic 256 px tile by putting the centre at (128, 128). That
    keeps ``px >> 8`` at zero and the tile lookup trivial.
    """

    SIZE = 256
    N = 16

    def _grid(self, block):
        """Sample a 16x16 grid over one tile, centred on pixel (128, 128)."""
        pixels = [(255, 0, 0, 255)
                  if (block[0] <= x < block[2] and block[1] <= y < block[3])
                  else (0, 0, 0, 0)
                  for y in range(self.SIZE) for x in range(self.SIZE)]
        png = radar.Png(make_png(self.SIZE, self.SIZE, pixels, ctype=6))
        return radar.sample_grid(lambda tx, ty: png, 128.0, 128.0, 64.0,
                                 self.N, radar.Palette())

    def test_a_storm_east_lands_in_the_east_half(self):
        columns = [i % self.N
                   for i, v in enumerate(self._grid((160, 96, 224, 160))) if v]
        self.assertTrue(columns, "no echo found at all")
        self.assertTrue(all(c > self.N // 2 for c in columns),
                        "echo appeared west of where it was painted")

    def test_a_storm_west_lands_in_the_west_half(self):
        columns = [i % self.N
                   for i, v in enumerate(self._grid((32, 96, 96, 160))) if v]
        self.assertTrue(columns)
        self.assertTrue(all(c < self.N // 2 for c in columns))

    def test_a_storm_north_lands_in_the_top_rows(self):
        rows = [i // self.N
                for i, v in enumerate(self._grid((96, 32, 160, 96))) if v]
        self.assertTrue(rows)
        self.assertTrue(all(r < self.N // 2 for r in rows),
                        "echo appeared below where it was painted")

    def test_a_single_speck_is_below_the_vote_floor(self):
        # One lit pixel cannot reach the 3-of-9 quorum, so isolated noise does
        # not become a cell of rain.
        self.assertEqual(set(self._grid((160, 160, 161, 161))), {0})

    def test_a_block_outside_the_region_samples_to_nothing(self):
        self.assertEqual(set(self._grid((300, 300, 301, 301))), {0})


class PayloadTests(unittest.TestCase):
    """The wire shape the browser depends on."""

    def _radar(self, frames):
        """A Radar wired to a fake RainViewer, keyed on the frame path."""
        def fetch(url):
            if "weather-maps" in url:
                return json.dumps({
                    "host": "https://example.invalid",
                    "radar": {"past": [{"time": 1000 + i, "path": "/p/%d" % i}
                                       for i in range(len(frames))]},
                }).encode()
            # The tile URL carries the frame path before the /256/ segment.
            return frames[int(re.search(r"/p/(\d+)/", url).group(1))]

        instance = radar.Radar(lat=40.7819, lon=21.4090, fetch=fetch)
        instance.refresh()
        return instance

    def test_a_dry_hour_ships_a_tiny_payload(self):
        blank = make_png(4, 4, [(0, 0, 0, 0)] * 16, ctype=6)
        instance = self._radar([blank] * radar.FRAMES)
        payload = instance.payload()
        self.assertLess(len(payload), 600)
        data = json.loads(payload.decode())
        self.assertEqual(data["n"], radar.GRID)
        self.assertEqual(len(data["frames"]), radar.FRAMES)
        self.assertEqual(data["palette"], [])
        for runs in data["frames"]:
            self.assertEqual(runs, [0, radar.GRID ** 2])

    def test_every_frame_carries_a_timestamp_the_client_can_format(self):
        blank = make_png(4, 4, [(0, 0, 0, 0)] * 16, ctype=6)
        data = json.loads(self._radar([blank] * radar.FRAMES).payload().decode())
        self.assertEqual(data["t"], sorted(data["t"]))
        self.assertEqual(len(data["t"]), len(data["frames"]))

    def test_a_failed_refresh_keeps_the_previous_payload(self):
        """A RainViewer outage must not blank the card."""
        blank = make_png(4, 4, [(0, 0, 0, 0)] * 16, ctype=6)
        instance = self._radar([blank] * radar.FRAMES)
        good = instance.payload()
        self.assertIsNotNone(good)

        def broken(url):
            raise OSError("network down")

        instance.fetch = broken
        with self.assertRaises(OSError):
            instance.refresh()
        self.assertEqual(instance.payload(), good)

    def test_frames_that_age_out_are_dropped(self):
        blank = make_png(4, 4, [(0, 0, 0, 0)] * 16, ctype=6)
        instance = self._radar([blank] * radar.FRAMES)
        self.assertEqual(len(instance._frames), radar.FRAMES)
        # A later index set replaces the old ones entirely.
        instance.fetch = lambda url: (
            json.dumps({
                "host": "https://example.invalid",
                "radar": {"past": [{"time": 9000 + i, "path": "/p/%d" % i}
                                   for i in range(radar.FRAMES)]},
            }).encode() if "weather-maps" in url else blank)
        instance.refresh()
        self.assertEqual(sorted(instance._frames), [9000 + i
                                                    for i in range(radar.FRAMES)])



class GeographyTests(unittest.TestCase):
    """The outlines that give the scopeless card something to orient by."""

    def test_the_data_is_where_it_should_be(self):
        import geography
        self.assertTrue(geography.LAKES)
        self.assertTrue(geography.BORDERS)
        # Every ring is at least a triangle, or it would not draw.
        for ring in list(geography.LAKES) + list(geography.BORDERS):
            self.assertGreaterEqual(len(ring), 3)
            for lon, lat in ring:
                self.assertTrue(-180 <= lon <= 180)
                self.assertTrue(-90 <= lat <= 90)

    def test_it_is_clipped_to_the_neighbourhood(self):
        """The whole world is 5 MB. This must be the few degrees around town."""
        import geography
        for ring in list(geography.LAKES) + list(geography.BORDERS):
            for lon, lat in ring:
                self.assertLess(abs(lat - 40.7819), 1.4)
                self.assertLess(abs(lon - 21.4090), 1.4)

    def test_it_stays_small_enough_to_send(self):
        """It is cached for a day, but it still has to be worth sending."""
        import json
        import geography
        payload = json.dumps({"lakes": geography.LAKES,
                              "borders": geography.BORDERS},
                             separators=(",", ":"))
        self.assertLess(len(payload), 12 * 1024)



class ConfigTests(unittest.TestCase):
    def test_the_radar_can_be_switched_off(self):
        import sources
        self.assertTrue(sources.Config().radar_enabled)


if __name__ == "__main__":
    unittest.main()
