# -*- coding: utf-8 -*-
"""Measures the Vitsi ridge leg of the inversion bias against station 230.

Run from the repository root:  python research/vitsi_ridge.py

The inversion index is a difference between two modelled temperatures, so a
bias common to both legs cancels and a bias that is not lands in the result.
`research/forecast_bias.py` settles the valley leg; this settles the ridge.

**The coordinates matter more than anything else here.** The first attempt at
this leg picked a forecast point by matching the station's barometer, and got
it ~800 m wrong — which is ~5 C of lapse rate, so the "bias" it reported was
mostly the height difference. Measuring a bias needs the station's actual
position, not a plausible one.

The station is VITSI (EMY / HNMS), dataset `emy-station-230`: 40.6417 N,
21.3833 E, ~1875 m, co-located with the 1st Regional Control & Warning Centre
radar installation on the summit. Three sources agree on the height - the
station record, the DEM at that point (1921 m) and the published figure.

Result as of the April-October 2026 record:

    point                          n     all   night     day
    published   40.6417, 21.3833  4526   -0.65   -0.30   -1.01
    geonames    40.6482, 21.3855  4526   +0.60   +0.95   +0.24
    app slope   40.7622, 21.3797  4526   -5.22   -4.61   -5.80

Against a valley night bias of -2.06 C for the same warm months (nine years,
`forecast_bias.py`), the difference the index is built from is understated by
roughly +1.8 C. `report.py` applies +0.73, so this has NOT been applied.

**The night window is worth more care than it looks.** Station 230 stamps in
UTC and so does the forecast request here, so local 21:00-06:00 is UTC
18:00-03:00 — a three-hour shift from the obvious reading. Getting that wrong
moved the night figure from -0.30 to +0.27 C, and it is not obvious from the
output that anything is wrong; the code succeeds either way. `forecast_bias.py`
sidesteps it by converting the station to local first, and the two agree
because both end up comparing the same local hours.

The Geonames point reads 1.25 C warmer at night, which is the elevation: the
DEM puts it 190 m higher, and 190 m of lapse is about 1.2 C. So the two are
consistent, and the published coordinate is the station.

Not applied, and why: the record covers no winter, which is when inversions
matter, and the station went down on 2026-10-06 before a full year could be
collected. Set `POINTS` below to re-measure once the position is confirmed.
"""
import csv
import io
import json
import os
import urllib.parse
import urllib.request

UA = {"User-Agent": "florina-weather/research"}
CACHE = os.path.join("research", "_cache")

STATION_ZIP = ("https://data.gov.gr/dataset/2832e42c-2aea-490d-b590-3b71682777e8"
               "/resource/08982d37-3906-4135-8901-656ff149d6bd/download/station_230.dat")

# (label, latitude, longitude) - edit these to test a different position.
POINTS = [
    ("published   40.6417, 21.3833", 40.6417, 21.3833),
    ("geonames    40.6482, 21.3855", 40.6482, 21.3855),
    ("app slope   40.7622, 21.3797", 40.7622, 21.3797),
]

# Europe/Athens is UTC+3 in summer time, so local hours 21-06 span these UTC
# hours. The station's stamps are UTC and so is the forecast request, so both
# sides are already on the same clock.
NIGHT = (18, 19, 20, 21, 22, 23, 0, 1, 2, 3)
DAY = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15)


def fetch(url, timeout=240):
    with urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout) as response:
        return response.read()


def cached(name, produce):
    """Keep downloads out of the repository, as the other research scripts do."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return open(path, "rb").read()
    blob = produce()
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    with open(path, "wb") as handle:
        handle.write(blob)
    return blob


def load_station():
    """Station 230's own five-minute records, keyed by the hour."""
    raw = cached("station_230.dat", lambda: fetch(STATION_ZIP))
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"))))
    header = [name.strip() for name in rows[0]]
    column = header.index("Temp_Dry_5min")
    out = {}
    for row in rows[1:]:
        if len(row) <= column:
            continue
        stamp, value = row[0].strip(), row[column].strip()
        if not value or value.startswith("/"):
            continue
        try:
            key = "%s-%s-%sT%s" % (stamp[0:4], stamp[4:6], stamp[6:8], stamp[8:10])
            out[key] = float(value)
        except ValueError:
            continue
    return out


def forecast_at(lat, lon, start, end):
    params = {"latitude": lat, "longitude": lon, "timezone": "UTC",
              "start_date": start, "end_date": end, "hourly": "temperature_2m"}
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode(params))
    return json.loads(fetch(url))["hourly"]


def main():
    station = load_station()
    if not station:
        raise SystemExit("no station readings - is data.gov.gr up?")
    start = min(station)[:10]
    end = max(station)[:10]
    print("station 230: %d hourly readings, %s .. %s" % (len(station), start, end))
    print()
    print("ridge leg, observed minus forecast   (negative = forecast too warm)")
    print("  %-28s %6s %7s %7s %7s" % ("point", "n", "all", "night", "day"))
    for label, lat, lon in POINTS:
        hourly = forecast_at(lat, lon, start, end)
        pairs = []
        for stamp, modelled in zip(hourly["time"], hourly["temperature_2m"]):
            if modelled is None:
                continue
            observed = station.get(stamp[:13])
            if observed is not None:
                pairs.append((int(stamp[11:13]), observed - modelled))
        if not pairs:
            print("  %-28s nothing paired" % label)
            continue
        night = [d for h, d in pairs if h in NIGHT]
        day = [d for h, d in pairs if h in DAY]
        print("  %-28s %6d %+7.2f %+7.2f %+7.2f"
              % (label, len(pairs),
                 sum(d for _, d in pairs) / len(pairs),
                 sum(night) / len(night) if night else float("nan"),
                 sum(day) / len(day) if day else float("nan")))
    print()
    print("  the valley leg is in research/forecast_bias.py; the index correction")
    print("  is the ridge figure minus the valley figure.")


if __name__ == "__main__":
    main()
