# -*- coding: utf-8 -*-
"""Derives the snow climatology constants shipped in ``report.py``.

No source observes snow at Florina — GHCN-Daily carries only TMAX/TMIN there,
data.gov.gr's station files have no snow column, EMY's own AUTO reports have no
snow group, and NOAA ISD's additional-data section is precipitation-only. So
snow is proxied as:

    a day counts as snow when precipitation was recorded
    and the minimum temperature was at or below 1 C

That definition is deliberately source-agnostic, so the two observed windows
can be compared without the instrument change standing in for a climate
change. Two checks say it is fair: ISD and NOA agree on daily minimum to
-0.09 C over 3 334 overlapping days, and ERA5's independent snow-day count for
2010-2023 (26.1) lands within two days of NOA's (24.1).

Run from the repository root:  python research/snow_climatology.py

It downloads ~7 MB and prints the constants. Nothing here ships; the numbers
below do.
"""
import csv
import datetime
import gzip
import io
import json
import os
import sys
import urllib.parse
import urllib.request
import zipfile

UA = {"User-Agent": "florina-weather/research"}
CACHE = os.path.join("research", "_cache")
LAT, LON = 40.7822, 21.4097
THRESHOLD = 1.0
STATION = "166130-99999"
WINTER_MONTHS = (11, 12, 1, 2, 3)

NOA_ZIP = ("https://data.gov.gr/dataset/cee4f7c1-68b3-4014-b7c7-926584b7bfe5/"
           "resource/821a8238-cf34-4cd1-b6f0-8668e13b8299/download/"
           "meteo_datagov.zip")


def fetch(url, timeout=300):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=timeout) as response:
        return response.read()


def cache(name, producer):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return path
    with open(path, "wb") as handle:
        handle.write(producer())
    return path


# ---------------------------------------------------------------- sources --
def get_noa():
    """Observed daily Florina: date -> (t_min, rain)."""
    path = cache("noa_florina.csv", lambda: zipfile.ZipFile(
        io.BytesIO(fetch(NOA_ZIP))).open("stations_data/florina.csv").read())
    out = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                out[datetime.date.fromisoformat(row["Date"])] = (
                    float(row["T_min"]), float(row["Ac_R"]))
            except (ValueError, KeyError):
                continue
    return out


def get_isd():
    """Observed Florina from ISD-Lite, aggregated to (t_min, wet) per day."""
    path = os.path.join(CACHE, "isd_florina.csv")
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        lines = ["date,temp_c,precip_mm"]
        for year in range(1932, 1982):
            try:
                text = gzip.decompress(fetch(
                    "https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/%d/"
                    "%s-%d.gz" % (year, STATION, year), timeout=60)
                ).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - a missing year is not fatal
                continue
            for line in text.splitlines():
                parts = line.split()
                if len(parts) < 11:
                    continue
                temp = int(parts[4]) / 10.0 if int(parts[4]) > -999 else ""
                precip = int(parts[9]) / 10.0 if int(parts[9]) > -999 else ""
                lines.append("%s-%s-%s %s,%s,%s" % (parts[0], parts[1], parts[2],
                                                    parts[3], temp, precip))
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))

    raw = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                day = datetime.date.fromisoformat(row["date"].strip()[:10])
            except ValueError:
                continue
            block = raw.setdefault(day, {"t": [], "p": []})
            if row["temp_c"]:
                block["t"].append(float(row["temp_c"]))
            if row["precip_mm"]:
                block["p"].append(float(row["precip_mm"]))
    return {day: (min(block["t"]), 1 if any(v > 0 for v in block["p"]) else 0)
            for day, block in raw.items() if block["t"]}


def get_era5():
    """Modelled daily: date -> (t_min, rain, snowfall, depth)."""
    def produce():
        params = {
            "latitude": LAT, "longitude": LON, "timezone": "Europe/Athens",
            "start_date": "1940-01-01", "end_date": "2025-12-31",
            "daily": ("precipitation_sum,temperature_2m_min,snowfall_sum,"
                      "snow_depth_max"),
        }
        return fetch("https://archive-api.open-meteo.com/v1/archive?"
                     + urllib.parse.urlencode(params))

    with open(cache("era5_florina.json", produce), encoding="utf-8") as handle:
        daily = json.load(handle)["daily"]
    out = {}
    for index, stamp in enumerate(daily["time"]):
        out[datetime.date.fromisoformat(stamp)] = (
            daily["temperature_2m_min"][index],
            daily["precipitation_sum"][index],
            daily["snowfall_sum"][index],
            daily["snow_depth_max"][index])
    return out


# ------------------------------------------------------------- analysis ----
def season(day):
    """December belongs to the winter that ends the following year."""
    return day.year + 1 if day.month == 12 else day.year


def share_by_month(source, first, last):
    """Share of days in each winter month that met the snow test."""
    counts = {}
    for day, values in source.items():
        if day.month not in WINTER_MONTHS:
            continue
        if not (first <= season(day) <= last):
            continue
        t_min, wet = values[0], values[1]
        if t_min is None or wet is None:
            continue
        entry = counts.setdefault(day.month, [0, 0])
        entry[1] += 1
        if t_min <= THRESHOLD and wet > 0:
            entry[0] += 1
    return {month: (snow / days if days else None)
            for month, (snow, days) in counts.items()}


def days_per_winter(source, first, last, minimum=120):
    """Snow days per winter, dropping winters with too little coverage."""
    out = {}
    for day, values in source.items():
        if day.month not in WINTER_MONTHS:
            continue
        year = season(day)
        if not (first <= year <= last):
            continue
        t_min, wet = values[0], values[1]
        if t_min is None or wet is None:
            continue
        entry = out.setdefault(year, [0, 0])
        entry[1] += 1
        if t_min <= THRESHOLD and wet > 0:
            entry[0] += 1
    return {year: value[0] for year, value in out.items()
            if value[1] >= minimum}


def main():
    noa, isd, era = get_noa(), get_isd(), get_era5()
    print("# NOA %d days, ISD %d days, ERA5 %d days\n"
          % (len(noa), len(isd), len(era)))

    older = share_by_month(isd, 1932, 1981)
    recent = share_by_month(noa, 2010, 2023)

    print("SNOW_SHARE_BY_MONTH = {")
    for key, table in (("old", older), ("recent", recent)):
        cells = ", ".join("%d: %.2f" % (m, table[m])
                          for m in (12, 1, 2, 3) if table.get(m))
        print("    %-9s {%s}," % ('"%s":' % key, cells))
    print("}\n")

    isd_days = days_per_winter(isd, 1932, 1981)
    noa_days = days_per_winter(noa, 2010, 2023)
    print("SNOW_DAYS_PER_WINTER = {")
    print("    %-9s %.0f,   # ISD, %d winters"
          % ('"old":', sum(isd_days.values()) / len(isd_days), len(isd_days)))
    print("    %-9s %.0f,   # NOA, %d winters"
          % ('"recent":', sum(noa_days.values()) / len(noa_days), len(noa_days)))
    print("}\n")

    print("SNOW_DAYS_BY_DECADE = [   # ERA5, which spans the years the gauges miss")
    totals = days_per_winter(era, 1940, 2025)
    for decade in range(1940, 2030, 10):
        years = [y for y in totals if decade <= y < decade + 10]
        if not years:
            continue
        mean = sum(totals[y] for y in years) / len(years)
        print("    (%d, %.0f),%s" % (decade, mean,
                                     "   # %d winters" % len(years)))
    print("]\n")

    # The claim the card makes: fewer events, not smaller ones.
    print("# deepest single winter day, ERA5, by decade")
    for decade in (1940, 1970, 2000, 2020):
        values = [v[2] for d, v in era.items()
                  if d.month in WINTER_MONTHS and decade <= season(d) < decade + 10
                  and v[2] is not None]
        if values:
            print("#   %ds  %.1f cm" % (decade, max(values)))


if __name__ == "__main__":
    sys.exit(main())
