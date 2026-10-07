# -*- coding: utf-8 -*-
"""Derives the forecast bias constants used by the inversion index in report.py.

Run from the repository root:  python research/forecast_bias.py

The inversion index is a difference between two modelled temperatures, so any
bias not common to both legs lands in it. This measures the valley leg against
the station over every year the archived-forecast API covers, and prints the
per-hour table the night figure is taken from.

The inversion index compares two modelled temperatures, so a bias that is
common to both cancels and a bias that is not does not. This measures the
valley leg against the station; the slope leg is a separate, weaker check.
"""
import csv
import json
import os
import urllib.parse
import urllib.request

UA = {"User-Agent": "florina-weather/research"}
CACHE = os.path.join("research", "_cache")
LAT, LON = 40.7822, 21.4097
FIRST, LAST = 2017, 2025


def fetch_year(year):
    path = os.path.join(CACHE, "fcst_%d.json" % year)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    params = {
        "latitude": LAT, "longitude": LON, "timezone": "Europe/Athens",
        "start_date": "%d-01-01" % year, "end_date": "%d-12-31" % year,
        "hourly": "temperature_2m",
    }
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode(params))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=180) as response:
        blob = response.read()
    with open(path, "wb") as handle:
        handle.write(blob)
    return json.loads(blob.decode("utf-8"))


def last_sunday(year, month):
    """The last Sunday of a month, as a date."""
    import calendar
    import datetime as dt
    day = calendar.monthrange(year, month)[1]
    while dt.date(year, month, day).weekday() != 6:
        day -= 1
    return dt.date(year, month, day)


def athens_offset(stamp):
    """Hours to add to a UTC stamp to get Athens wall time.

    The station's stamps are UTC and the forecast is local, so comparing them
    without this puts a three-hour shift into the result that looks exactly
    like a diurnal bias. The EU rule is the last Sunday in March to the last
    Sunday in October; a fixed +3 would be wrong for half the year.
    """
    import datetime as dt
    day = dt.date(int(stamp[:4]), int(stamp[5:7]), int(stamp[8:10]))
    return 3 if last_sunday(day.year, 3) <= day < last_sunday(day.year, 10) else 2


def to_local(stamp):
    """A UTC 'YYYY-MM-DD HH' stamp as a local 'YYYY-MM-DDTHH' key."""
    import datetime as dt
    when = dt.datetime(int(stamp[:4]), int(stamp[5:7]), int(stamp[8:10]),
                       int(stamp[11:13])) + dt.timedelta(hours=athens_offset(stamp))
    return when.strftime("%Y-%m-%dT%H")


# The station's own record, three-hourly, already cached by the snow work.
observed = {}
with open(os.path.join(CACHE, "isd_florina.csv"), encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        stamp = row["date"].strip()
        if not row["temp_c"]:
            continue
        if int(stamp[:4]) < FIRST:
            continue
        observed[to_local(stamp)] = float(row["temp_c"])

print("station hours 2017-2025 : %d" % len(observed))

paired = []          # (hour_of_day, month, observed - modelled)
for year in range(FIRST, LAST + 1):
    try:
        hourly = fetch_year(year)["hourly"]
    except Exception as exc:  # noqa: BLE001
        print("  %d failed: %s" % (year, str(exc)[:50]))
        continue
    got = 0
    for stamp, value in zip(hourly["time"], hourly["temperature_2m"]):
        if value is None:
            continue
        key = stamp[:13]
        if key in observed:
            paired.append((int(stamp[11:13]), int(stamp[5:7]),
                           observed[key] - value))
            got += 1
    print("  %d: %5d paired hours" % (year, got))

print()
print("total paired hours      : %d" % len(paired))
if not paired:
    raise SystemExit("nothing paired")

diffs = sorted(d for _, _, d in paired)
n = len(diffs)
print("overall bias            : %+.2f C  (observed minus forecast)" % (sum(diffs) / n))
print("mean absolute           : %.2f C" % (sum(abs(d) for d in diffs) / n))

print()
print("BY HOUR OF DAY  (negative means the forecast runs warm)")
print("  hour   n     bias")
by_hour = {}
for hour, _, diff in paired:
    by_hour.setdefault(hour, []).append(diff)
for hour in sorted(by_hour):
    values = by_hour[hour]
    print("   %02d:00 %5d  %+6.2f C  %s"
          % (hour, len(values), sum(values) / len(values),
             "#" * int(abs(sum(values) / len(values)) * 10)))

# The index only matters when an inversion is plausible: night, and the cold
# half of the year. Averaging over July afternoons would hide it.
NIGHT = (21, 22, 23, 0, 1, 2, 3, 4, 5, 6)


def subset(label, predicate):
    values = [d for h, m, d in paired if predicate(h, m)]
    if len(values) < 40:
        print("  %-34s too few (%d)" % (label, len(values)))
        return
    print("  %-34s n=%5d  bias %+6.2f C" % (label, len(values), sum(values) / len(values)))


print()
print("THE SUBSETS THAT MATTER")
subset("all hours", lambda h, m: True)
subset("night (21:00-06:00)", lambda h, m: h in NIGHT)
subset("cold months, night", lambda h, m: h in NIGHT and m in (11, 12, 1, 2, 3))
subset("warm months, night", lambda h, m: h in NIGHT and m in (5, 6, 7, 8, 9))
subset("day (09:00-18:00)", lambda h, m: 9 <= h <= 18)
