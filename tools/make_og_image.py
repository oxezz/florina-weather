# -*- coding: utf-8 -*-
"""Draws ``og-image.png``, the card used for link previews and the repo.

Run from the repository root:  python tools/make_og_image.py

The curve is a real 48-hour temperature trace from the same API the app uses,
so the image shows what the thing actually does rather than a decorative
squiggle. If the network is unavailable it falls back to a representative
shape and says so on stderr.

Only the standard library and Pillow, which is bundled with the toolchain —
nothing here is a runtime dependency of the app.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
LAT, LON = 40.7822, 21.4097

# The app's own palette, so the card and the page look like the same thing.
BG_TOP = (8, 28, 48)
BG_BOTTOM = (14, 46, 74)
INK = (238, 246, 252)
DIM = (150, 180, 205)
FAINT = (108, 140, 168)
ACCENT = (96, 176, 240)
CURVE = (250, 204, 21)

FALLBACK = [8.5, 8.1, 7.6, 7.0, 6.6, 6.4, 6.9, 8.2, 10.1, 12.0, 13.6, 14.8,
            15.5, 15.8, 15.4, 14.6, 13.4, 12.2, 11.3, 10.6, 10.0, 9.5, 9.0,
            8.6, 8.2, 7.9, 7.5, 7.1, 6.8, 6.5, 6.3, 6.2, 6.8, 8.0, 9.8,
            11.6, 13.2, 14.4, 15.1, 15.3, 14.9, 14.0, 12.9, 11.9, 11.1,
            10.4, 9.8, 9.3]


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def temperature_trace():
    """A real 48-hour temperature series, or the fallback."""
    params = {
        "latitude": LAT, "longitude": LON, "timezone": "Europe/Athens",
        "hourly": "temperature_2m", "forecast_days": 3,
    }
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            hourly = json.load(response)["hourly"]
        values = [v for v in hourly["temperature_2m"] if v is not None][:48]
        if len(values) >= 24:
            return values, True
    except Exception as exc:  # noqa: BLE001
        print("forecast unavailable (%s), using the fallback shape" % exc,
              file=sys.stderr)
    return FALLBACK, False


def gradient():
    """The page backdrop: a vertical ramp, as the app paints it."""
    image = Image.new("RGB", (1, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        share = y / float(HEIGHT - 1)
        draw.point((0, y), fill=tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * share) for i in range(3)))
    return image.resize((WIDTH, HEIGHT))


def draw_curve(draw, values, top, height, pad):
    """The temperature line, with a soft fill beneath it."""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    usable = WIDTH - pad * 2
    points = []
    for index, value in enumerate(values):
        x = pad + usable * index / float(len(values) - 1)
        y = top + height - height * (value - low) / span
        points.append((x, y))

    # A translucent fill under the line: a second image so the alpha works.
    # Pale blue rather than the line's own yellow, which over a blue backdrop
    # mixes to a muddy green.
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    shade = ImageDraw.Draw(layer)
    shade.polygon(points + [(points[-1][0], top + height),
                            (points[0][0], top + height)],
                  fill=(150, 200, 240, 30))
    return layer, points


def main():
    values, real = temperature_trace()
    image = gradient().convert("RGBA")
    draw = ImageDraw.Draw(image)

    pad = 78
    draw.text((pad, 96), "Φλώρινα", font=font("segoeuib.ttf", 78), fill=INK)
    draw.text((pad, 196), "Δυτική Μακεδονία", font=font("segoeui.ttf", 30),
              fill=DIM)
    draw.text((pad, 258), "Καιρός · αέρας · τοπικό μικροκλίμα",
              font=font("segoeui.ttf", 26), fill=FAINT)

    layer, points = draw_curve(draw, values, top=330, height=150, pad=pad)
    image = Image.alpha_composite(image, layer)
    draw = ImageDraw.Draw(image)
    draw.line(points, fill=CURVE, width=4, joint="curve")
    for index in (0, len(points) // 2, len(points) - 1):
        x, y = points[index]
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=CURVE)
    # The peak, marked, because it is the thing the eye looks for.
    peak = points[values.index(max(values))]
    draw.ellipse([peak[0] - 5, peak[1] - 5, peak[0] + 5, peak[1] + 5],
                 fill=(255, 255, 255))
    draw.text((pad, 500), "%.1f° τώρα   ·   60 → 24 μέρες χιόνι   ·   "
                          "σταθμός ΕΜΥ μέσα στην πόλη"
              % values[0], font=font("segoeui.ttf", 24),
              fill=(196, 218, 236))

    draw.line([(pad, 566), (WIDTH - pad, 566)], fill=(40, 78, 112), width=1)
    draw.text((pad, 584), "Python · χωρίς εξαρτήσεις",
              font=font("segoeui.ttf", 20), fill=FAINT)
    footer = "florina-weather.wasmer.app"
    width = draw.textlength(footer, font=font("segoeui.ttf", 20))
    draw.text((WIDTH - pad - width, 584), footer,
              font=font("segoeui.ttf", 20), fill=FAINT)

    out = "og-image.png"
    image.convert("RGB").save(out, "PNG", optimize=True)
    print("%s written: %dx%d, %.1f KB, curve is %s"
          % (out, WIDTH, HEIGHT, os.path.getsize(out) / 1024.0,
             "live forecast data" if real else "the fallback shape"))


if __name__ == "__main__":
    sys.exit(main())
