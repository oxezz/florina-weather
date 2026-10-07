# -*- coding: utf-8 -*-
"""Turns raw upstream payloads into the view model the front-end consumes.

Pure and deterministic: every function takes the data it needs (including
"now") as an argument, so the tests can pin a fake clock and synthetic
payloads and check the output exactly.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import greek


class ReportError(ValueError):
    """Raised when the forecast payload is missing something essential."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _at(values, index, default=None):
    """Safe list indexing for the parallel arrays Open-Meteo returns."""
    try:
        if values is None:
            return default
        value = values[index]
    except (IndexError, TypeError, KeyError):
        return default
    return default if value is None else value


def _is_number(value):
    """True for a real, finite number — ``None``, strings and NaN all fail."""
    try:
        return value is not None and float(value) == float(value)
    except (TypeError, ValueError):
        return False


def _zone(name):
    """IANA zone, or ``None`` when this Python has no timezone database.

    A stock Windows Python ships without ``tzdata``, so this is expected to
    return ``None`` there; :func:`resolve_timezone` handles that case.
    """
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError and friends
        return None


def resolve_timezone(name, forecast=None):
    """Return ``(tzinfo, source)`` for the configured location.

    Prefers the IANA database because it tracks DST changes inside the
    forecast window. When that is unavailable we fall back to the UTC offset
    Open-Meteo reports for the coordinates, which is authoritative and needs
    no database at all.
    """
    zone = _zone(name)
    if zone is not None:
        return zone, "zoneinfo"
    offset = (forecast or {}).get("utc_offset_seconds")
    if isinstance(offset, (int, float)) and not isinstance(offset, bool):
        return timezone(timedelta(seconds=offset)), "utc_offset"
    return timezone.utc, "utc"


def _parse_local(value, tz):
    """Parse an Open-Meteo local timestamp (no offset) into a naive datetime.

    ``tz`` may be ``None``; it is only consulted when the timestamp actually
    carries an offset (which Open-Meteo does not emit for a named timezone).
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        name = getattr(tz, "key", None) or (str(tz) if tz else "UTC")
        parsed = parsed.astimezone(_zone(name) or timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_utc(value, tz):
    """Parse a CAP timestamp (UTC) and return it as aware local time."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def _normalise(text):
    """Casefold, strip accents and HTML entities, collapse whitespace."""
    if not text:
        return ""
    decoded = html.unescape(str(text))
    decomposed = unicodedata.normalize("NFD", decoded)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


# --------------------------------------------------------------------------
# Alerts (Meteoalarm CAP feed)
# --------------------------------------------------------------------------


def _alert_info_blocks(alert):
    """Prefer the Greek info block; fall back to whatever is available."""
    infos = alert.get("info") or []
    greek_blocks = [i for i in infos if str(i.get("language", "")).startswith("el")]
    return greek_blocks or infos


def _awareness(block):
    """Extract ``(hazard_code, awareness_level)`` from a CAP info block."""
    hazard_code = None
    level = None
    for parameter in block.get("parameter") or []:
        name = str(parameter.get("valueName", "")).lower()
        value = str(parameter.get("value", ""))
        if name == "awareness_type":
            hazard_code = value.split(";")[-1].strip() if ";" in value else value.strip()
        elif name == "awareness_level":
            level = value
    return hazard_code, level


def area_matches(area_desc, geocodes, config):
    """Is this Meteoalarm area relevant to our town?"""
    aliases = [a for a in (config.alert_areas or []) if a]
    emma_ids = [i for i in (config.alert_emma_ids or []) if i]
    if not aliases and not emma_ids:
        return True
    for code in geocodes:
        if code and code in emma_ids:
            return True
    normalised = _normalise(area_desc)
    if not normalised:
        return False
    return any(_normalise(alias) in normalised for alias in aliases)


def parse_alerts(raw, config, now):
    """Normalise the Meteoalarm feed into a flat, sorted list of alerts."""
    if not raw:
        return []
    warns = raw.get("warnings") if isinstance(raw, dict) else raw
    if not warns:
        return []

    results = []
    seen = set()
    for wrapper in warns:
        alert = (wrapper or {}).get("alert") or {}
        if str(alert.get("msgType", "")).lower() == "cancel":
            continue
        if str(alert.get("incidents", "")).lower() == "cancel":
            continue
        identifier = alert.get("identifier") or ""
        for block in _alert_info_blocks(alert):
            expires = _parse_utc(block.get("expires"), now.tzinfo)
            if expires is not None and expires < now:
                continue
            hazard_code, level_param = _awareness(block)
            level_key, level_label, colour = greek.warning_level(
                level_param, block.get("severity"))
            hazard_label, hazard_icon = greek.hazard(hazard_code)
            areas = block.get("area") or [{}]
            for area in areas:
                area_desc = html.unescape(str(area.get("areaDesc") or "")).strip()
                geocodes = [g.get("value") for g in (area.get("geocode") or [])]
                if not area_matches(area_desc, geocodes, config):
                    continue
                key = (identifier, hazard_code, level_key, _normalise(area_desc))
                if key in seen:
                    continue
                seen.add(key)
                onset = _parse_utc(block.get("onset") or block.get("effective"), now.tzinfo)
                results.append({
                    "identifier": identifier,
                    "level": level_key,
                    "level_label": level_label,
                    "color": colour,
                    "hazard": hazard_label,
                    "icon": hazard_icon,
                    "area": area_desc,
                    "headline": html.unescape(str(block.get("headline") or "")).strip(),
                    "description": html.unescape(str(block.get("description") or "")).strip(),
                    "certainty": block.get("certainty"),
                    "onset": onset.strftime("%Y-%m-%dT%H:%M") if onset else None,
                    "expires": expires.strftime("%Y-%m-%dT%H:%M") if expires else None,
                    "onset_text": _window_text(onset, now),
                    "expires_text": _window_text(expires, now),
                    "web": block.get("web") or "https://www.meteoalarm.org",
                    "_rank": greek.warning_rank(level_key),
                    "_onset_sort": onset.timestamp() if onset else float("inf"),
                })

    results.sort(key=lambda a: (-a["_rank"], a["_onset_sort"]))
    for item in results:
        item.pop("_rank", None)
        item.pop("_onset_sort", None)
    return results


def _window_text(moment, now):
    """``"Σήμερα 21:00"`` / ``"Αύριο 03:00"`` / ``"8/10 06:00"``."""
    if moment is None:
        return "—"
    local_date = moment.date()
    delta = (local_date - now.date()).days
    clock = moment.strftime("%H:%M")
    if delta == 0:
        return "Σήμερα %s" % clock
    if delta == 1:
        return "Αύριο %s" % clock
    if delta == -1:
        return "Χθες %s" % clock
    return "%d/%d %s" % (local_date.day, local_date.month, clock)


# --------------------------------------------------------------------------
# Air quality
# --------------------------------------------------------------------------

_POLLENS = (
    ("alder_pollen", "Γύρη σκλήθρου"),
    ("birch_pollen", "Γύρη σημύδας"),
    ("grass_pollen", "Γύρη γρασιδιού"),
    ("mugwort_pollen", "Γύρη αψιθιάς"),
    ("olive_pollen", "Γύρη ελιάς"),
    ("ragweed_pollen", "Γύρη αμβροσίας"),
)


def parse_air(raw):
    """Normalise the Open-Meteo air-quality payload."""
    if not raw:
        return None
    current = raw.get("current") or {}
    if not current:
        return None
    aqi = current.get("european_aqi")
    label, colour = greek.aqi_level(aqi)
    pollens = []
    for key, name in _POLLENS:
        value = current.get(key)
        if isinstance(value, (int, float)) and value > 0:
            pollens.append({"name": name, "value": round(float(value), 1)})
    pollens.sort(key=lambda p: -p["value"])
    return {
        "time": current.get("time"),
        "aqi": aqi,
        "label": label,
        "color": colour,
        "pm2_5": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "pollens": pollens,
    }


# --------------------------------------------------------------------------
# Hyper-local conditions
# --------------------------------------------------------------------------
#
# Florina sits in a basin at ~660 m, which gives the town a climate of its own:
# a long heating season, hard winter inversions, wood-smoke that pools on calm
# evenings, and late/early frosts that matter to the apple and pepper growers.
# Nothing here is invented — each block is a straight reading of data we
# already have, and each is simply absent when it has nothing to say.

HDD_BASE = 18.0          # the conventional heating-degree-day base, °C
SMOG_EVENING_FROM = 18   # the wood-smoke window runs 18:00 -> 02:00
SMOG_EVENING_TO = 2
SMOG_CALM_KMH = 12.0     # above this the valley ventilates itself

STANDARD_LAPSE = 0.65    # °C lost per 100 m of ascent, standard atmosphere


def _model_series(block, prefix):
    """``{model: [values]}`` from an Open-Meteo block with suffixed keys.

    With several models requested, Open-Meteo names the variables
    ``temperature_2m_icon_eu`` and so on. ``current`` is the exception: it
    comes back unsuffixed, which is why the inversion reads from ``hourly``.
    """
    out = {}
    for key, values in (block or {}).items():
        if not key.startswith(prefix) or not isinstance(values, list):
            continue
        model = key[len(prefix):].strip("_") or "default"
        out[model] = values
    return out


def build_agreement(terrain, config):
    """Per-date spread between the models, for the daily cards."""
    if not isinstance(terrain, list) or not terrain:
        return {}
    daily = terrain[0].get("daily") or {}
    times = daily.get("time") or []
    maxima = _model_series(daily, "temperature_2m_max")
    minima = _model_series(daily, "temperature_2m_min")
    if not times or not maxima:
        return {}

    spread = {}
    for index, stamp in enumerate(times):
        highs = [v[index] for v in maxima.values()
                 if index < len(v) and v[index] is not None]
        lows = [v[index] for v in minima.values()
                if index < len(v) and v[index] is not None]
        # Two models is the minimum for a disagreement to mean anything.
        if len(highs) < 2:
            continue
        high_spread = max(highs) - min(highs)
        low_spread = (max(lows) - min(lows)) if len(lows) > 1 else 0.0
        worst = max(high_spread, low_spread)
        key, label, colour = greek.agreement_level(worst)
        spread[str(stamp)] = {
            "spread": round(worst, 1),
            "level": key,
            "label": label,
            "color": colour,
            "models": len(highs),
            # The headline number is the wider of the two, so both ranges are
            # reported and the tooltip says which one it came from. Quoting
            # only the highs left "±2.9" sitting next to a 19.8-20.2 range.
            "driver": "max" if high_spread >= low_spread else "min",
            "range": [round(min(highs), 1), round(max(highs), 1)],
            "low_range": ([round(min(lows), 1), round(max(lows), 1)]
                          if len(lows) > 1 else None),
        }
    return spread


def build_inversion(terrain, config, now_local):
    """The basin's thermal inversion, read from the valley against a slope.

    A standard atmosphere loses 0.65 °C per 100 m. When the slope comes out
    *warmer* than that predicts, cold air has pooled in the basin — which is
    the whole reason Florina is colder than the villages above it.

    The anomaly is averaged over the models, and their disagreement is
    reported alongside it, because a single model's claim here is not worth
    much on its own.
    """
    if not isinstance(terrain, list) or len(terrain) < 2:
        return None
    valley, slope = terrain[0], terrain[1]
    z_low, z_high = valley.get("elevation"), slope.get("elevation")
    if not z_low or not z_high or z_high <= z_low:
        return None
    rise = z_high - z_low
    expected = STANDARD_LAPSE * rise / 100.0

    floor = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None)

    def reading(location):
        hourly = location.get("hourly") or {}
        times = hourly.get("time") or []
        index = None
        for position, stamp in enumerate(times):
            parsed = _parse_local(str(stamp), None)
            if parsed is not None and parsed >= floor:
                index = position
                break
        if index is None:
            return {}
        found = {}
        for model, values in _model_series(hourly, "temperature_2m").items():
            if index < len(values) and values[index] is not None:
                found[model] = values[index]
        return found

    low, high = reading(valley), reading(slope)
    shared = sorted(set(low) & set(high))
    if not shared:
        return None

    anomalies = [high[m] - low[m] + expected for m in shared]
    mean_anomaly = sum(anomalies) / len(anomalies)
    spread = max(anomalies) - min(anomalies)
    level = greek.inversion_level(mean_anomaly)
    if level is None:
        return None
    key, label, colour = level

    # If the models disagree by more than the anomaly itself, some of them are
    # saying there is no inversion at all. Report it as a hint, not a fact.
    confident = spread < abs(mean_anomaly)
    if not confident:
        key, label, colour = greek.UNCERTAIN_INVERSION

    valley_temp = sum(low[m] for m in shared) / len(shared)
    slope_temp = sum(high[m] for m in shared) / len(shared)
    delta = slope_temp - valley_temp
    return {
        "level": key,
        "label": label,
        "color": colour,
        "confident": confident,
        "delta": round(delta, 1),
        "anomaly": round(mean_anomaly, 1),
        "peak_anomaly": round(max(anomalies), 1),
        "lapse": round(delta / rise * 100.0, 2),
        "valley_temp": round(valley_temp, 1),
        "slope_temp": round(slope_temp, 1),
        "valley_elev": round(z_low),
        "slope_elev": round(z_high),
        "slope_name": config.slope_name,
        "models": len(shared),
        # How much the models disagree about the anomaly itself.
        "spread": round(spread, 1),
    }


def _air_hourly(air):
    """The hourly block of an air-quality payload, if there is one."""
    if not isinstance(air, dict):
        return None
    hourly = air.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        return None
    return hourly


def _surface_minima(hourly):
    """Coldest ground-surface temperature per date, from the hourly series.

    Only the 0 cm layer is a frost sensor. At 6 cm the soil stays 5-8 °C
    warmer than the surface, which is root-zone warmth, not frost risk.
    """
    times = (hourly or {}).get("time") or []
    values = (hourly or {}).get("soil_temperature_0cm") or []
    by_day = {}
    for stamp, value in zip(times, values):
        if value is None:
            continue
        try:
            day = str(stamp)[:10]
        except Exception:  # noqa: BLE001
            continue
        if day not in by_day or value < by_day[day]:
            by_day[day] = value
    return by_day


def build_frost(days, forecast_hourly=None):
    """Frost risk across the forecast window, for growers.

    Two sensors, because they disagree: the 2 m air minimum and the ground
    surface. On a clear calm night the surface radiates heat away and runs
    1-3 °C colder, so it can freeze while the air is still above zero — which
    is exactly the radiation frost that catches low crops such as peppers.

    Returns ``None`` when no night in range comes near freezing, which keeps
    the card off the page from late spring to early autumn.
    """
    surface = _surface_minima(forecast_hourly)
    nights = []
    for day in days or []:
        air = day.get("min")
        ground = surface.get(day.get("iso"))
        readings = [value for value in (air, ground) if value is not None]
        if not readings:
            continue
        effective = min(readings)
        level = greek.frost_level(effective)
        if level is None:
            continue
        nights.append({
            "label": day["label"],
            "iso": day["iso"],
            "weekday": day["weekday"],
            "air": air,
            "ground": ground,
            "min": effective,
            "level": level[0],
        })
    if not nights:
        return None

    coldest = min(nights, key=lambda night: night["min"])
    key, label, colour = greek.frost_level(coldest["min"])
    frosty = [night for night in nights if night["min"] <= 0]
    air_values = [n["air"] for n in nights if n["air"] is not None]
    ground_values = [n["ground"] for n in nights if n["ground"] is not None]
    return {
        "level": key,
        "label": label,
        "color": colour,
        "min": coldest["min"],
        "air_min": min(air_values) if air_values else None,
        "ground_min": min(ground_values) if ground_values else None,
        # Which sensor drove the warning: the surface almost always does.
        "driver": ("ground" if coldest["ground"] is not None
                   and (coldest["air"] is None or coldest["ground"] <= coldest["air"])
                   else "air"),
        "lowest_label": coldest["label"],
        "count": len(frosty),
        "first": nights[0],
        "nights": nights,
    }


def build_heating(history, now_local):
    """Heating degree days: today, and the month so far."""
    if not isinstance(history, dict):
        return None
    # Accept either the raw payload or its "daily" block.
    if isinstance(history.get("daily"), dict):
        history = history["daily"]
    times = history.get("time") or []
    means = history.get("temperature_2m_mean") or []
    if not times or not means:
        return None

    prefix = now_local.strftime("%Y-%m")
    month_values = []
    today_mean = None
    for stamp, mean in zip(times, means):
        if mean is None:
            continue
        today_mean = mean  # the last usable entry is today
        if str(stamp).startswith(prefix):
            month_values.append(mean)

    if not month_values or today_mean is None:
        return None

    month_hdd = sum(max(0.0, HDD_BASE - value) for value in month_values)
    if month_hdd <= 0.0:
        return None  # nothing to heat: hide it for the summer

    return {
        "base": HDD_BASE,
        "today": round(max(0.0, HDD_BASE - today_mean), 1),
        "today_mean": round(today_mean, 1),
        "month": round(month_hdd, 1),
        "month_days": len(month_values),
        "month_name": greek.month_name(now_local),
    }


def build_smog(air_hourly, forecast_hourly, now_local):
    """Evening particulate build-up on calm nights — the wood-smoke signature.

    Reports the peak PM2.5 in tonight's window, the wind that goes with it, and
    the cleanest hour of the next day, which is the actionable part.
    """
    if not air_hourly:
        return None
    times = air_hourly.get("time") or []
    values = air_hourly.get("pm2_5") or []
    if not times or not values:
        return None

    wind_by_time = {}
    for stamp, speed in zip((forecast_hourly or {}).get("time") or [],
                            (forecast_hourly or {}).get("wind_speed_10m") or []):
        wind_by_time[str(stamp)] = speed

    start = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    end = start + timedelta(hours=24)

    evening = []
    whole_day = []
    for stamp, value in zip(times, values):
        if value is None:
            continue
        parsed = _parse_local(str(stamp), None)
        if parsed is None or not (start <= parsed <= end):
            continue
        whole_day.append((parsed, value))
        hour = parsed.hour
        if hour >= SMOG_EVENING_FROM or hour < SMOG_EVENING_TO:
            evening.append((parsed, value, wind_by_time.get(str(stamp))))

    if not evening:
        return None

    peak_at, peak_value, peak_wind = max(evening, key=lambda item: item[1])
    level = greek.smog_level(peak_value)
    if level is None:
        return None  # air is clean; the air-quality panel already covers it

    winds = [w for _t, _v, w in evening if w is not None]
    mean_wind = sum(winds) / len(winds) if winds else None
    # Wood smoke only pools when the valley is calm. The same PM2.5 with a real
    # breeze is particulate passing through, not smog sitting on the town, and
    # the ventilation advice does not apply.
    pooling = mean_wind is None or mean_wind < SMOG_CALM_KMH
    cleanest_at, cleanest_value = min(whole_day, key=lambda item: item[1])

    key, label, colour = level
    if not pooling:
        label = "Αυξημένα σωματίδια"
    return {
        "level": key,
        "label": label,
        "color": colour,
        "peak": round(peak_value, 1),
        "peak_time": peak_at.strftime("%H:%M"),
        "wind": round(mean_wind, 1) if mean_wind is not None else None,
        "pooling": pooling,
        "calm": mean_wind is not None and mean_wind < SMOG_CALM_KMH,
        "cleanest_time": cleanest_at.strftime("%H:%M"),
        "cleanest": round(cleanest_value, 1),
    }


def build_local_conditions(snapshot, days, forecast_hourly, now_local, config,
                           snow=None):
    """Assemble the hyper-local block, dropping anything with nothing to say.

    The mountain road and the snow depth used to be two cards here. They are
    now one seasonal card of their own, because they answer the same question
    on the same day and read the same forecast — see :func:`build_mountain`.
    """
    blocks = {
        "inversion": build_inversion(snapshot.get("terrain"), config, now_local),
        "frost": build_frost(days, forecast_hourly),
        "heating": build_heating(snapshot.get("history"), now_local),
        "smog": build_smog(_air_hourly(snapshot.get("air")),
                           forecast_hourly, now_local),
    }
    present = {key: value for key, value in blocks.items() if value}
    if not present:
        return None
    return present


# --------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------


def _hourly_points(hourly, start_index, count, tz, moon=None):
    times = hourly.get("time") or []
    points = []
    for offset in range(count):
        index = start_index + offset
        if index >= len(times):
            break
        stamp = times[index]
        parsed = _parse_local(stamp, tz)
        code = _at(hourly.get("weather_code"), index, 0)
        is_day = bool(_at(hourly.get("is_day"), index, 1))
        # A clear night shows the real moon, matching the hero and the moon
        # row, rather than a stock crescent that contradicts them.
        emoji = greek.emoji(code, is_day)
        if moon and not is_day and code in (0, 1):
            emoji = moon["emoji"]
        direction = _at(hourly.get("wind_direction_10m"), index)
        points.append({
            "iso": str(stamp),
            "time": greek.format_hhmm(str(stamp)),
            "temp": _at(hourly.get("temperature_2m"), index),
            "apparent": _at(hourly.get("apparent_temperature"), index),
            "humidity": _at(hourly.get("relative_humidity_2m"), index),
            "precip_prob": _at(hourly.get("precipitation_probability"), index, 0),
            "precip": _at(hourly.get("precipitation"), index, 0.0),
            "code": code,
            "text": greek.describe(code),
            "emoji": emoji,
            "is_day": is_day,
            "wind": _at(hourly.get("wind_speed_10m"), index),
            "gusts": _at(hourly.get("wind_gusts_10m"), index),
            "wind_dir": direction,
            "wind_dir_text": greek.compass(direction) if direction is not None else None,
            "wind_arrow": greek.wind_arrow(direction) if direction is not None else None,
            "uv": _at(hourly.get("uv_index"), index),
            "cloud": _at(hourly.get("cloud_cover"), index),
            "dew_point": _at(hourly.get("dew_point_2m"), index),
            "radiation": _at(hourly.get("shortwave_radiation"), index),
            "freezing": _at(hourly.get("freezing_level_height"), index),
            "visibility": _at(hourly.get("visibility"), index),
            "visibility_text": greek.visibility_text(_at(hourly.get("visibility"), index)),
            "day": parsed.date().isoformat() if parsed else None,
        })
    return points


def _day_icon(hourly, day_iso, daily_code, precip_sum, precip_prob):
    """Pick a day's icon from its *daylight* hours.

    Open-Meteo's daily ``weather_code`` is the most severe hour of the 24, so
    it reports a thunderstorm for 0.8 mm of rain and a cloudy icon for a day
    that was clear from sunrise to sunset. Reading the daytime hours instead
    gives the icon people actually experience.
    """
    times = (hourly or {}).get("time") or []
    codes = (hourly or {}).get("weather_code") or []
    cloud = (hourly or {}).get("cloud_cover") or []
    daylight = (hourly or {}).get("is_day") or []

    daytime_codes, clouds = [], []
    for index, stamp in enumerate(times):
        if str(stamp)[:10] != day_iso:
            continue
        if not _at(daylight, index, 1):
            continue                    # the night is not what the day looked like
        code = _at(codes, index)
        if code is not None:
            daytime_codes.append(code)
        cover = _at(cloud, index)
        if cover is not None:
            clouds.append(cover)

    wet = ((_is_number(precip_sum) and precip_sum >= 1.0) or
           (_is_number(precip_prob) and precip_prob >= 40))

    if wet:
        frozen = (any(c in _CODES_SNOW for c in daytime_codes) or
                  daily_code in _CODES_SNOW)
        if frozen:
            return daily_code if daily_code in _CODES_SNOW else 73
        # Thunder needs real rain behind it, not a 0.8 mm squall.
        if (daily_code in _CODES_STORM and _is_number(precip_sum)
                and precip_sum >= 1.0):
            return daily_code
        if daily_code in _CODES_RAIN:
            return daily_code
        return 61
    if daily_code in _CODES_FOG or any(c in _CODES_FOG for c in daytime_codes):
        return 45
    if not clouds:
        return daily_code               # no daytime data: trust the API
    mean = sum(clouds) / len(clouds)
    if mean < 15:
        return 0
    if mean < 40:
        return 1
    if mean < 70:
        return 2
    return 3


def _daily_points(daily, tz, today, config, agreement=None, hourly=None):
    times = daily.get("time") or []
    points = []
    for index, stamp in enumerate(times):
        try:
            day = date.fromisoformat(str(stamp))
        except ValueError:
            continue
        code = _day_icon(hourly, str(stamp),
                         _at(daily.get("weather_code"), index, 0),
                         _at(daily.get("precipitation_sum"), index, 0.0),
                         _at(daily.get("precipitation_probability_max"), index, 0))
        uv_max = _at(daily.get("uv_index_max"), index)
        uv_label, uv_colour = greek.uv_level(uv_max)
        direction = _at(daily.get("wind_direction_10m_dominant"), index)
        points.append({
            "iso": str(stamp),
            "label": greek.day_label(day, today),
            "weekday": greek.weekday_name(day),
            "weekday_short": greek.weekday_short(day),
            "day": day.day,
            "month": greek.month_name(day),
            "code": code,
            "text": greek.describe(code),
            "emoji": greek.emoji(code, True),
            "min": _at(daily.get("temperature_2m_min"), index),
            "max": _at(daily.get("temperature_2m_max"), index),
            "apparent_min": _at(daily.get("apparent_temperature_min"), index),
            "apparent_max": _at(daily.get("apparent_temperature_max"), index),
            "precip_sum": _at(daily.get("precipitation_sum"), index, 0.0),
            "precip_prob": _at(daily.get("precipitation_probability_max"), index, 0),
            "sunrise": greek.format_hhmm(_at(daily.get("sunrise"), index)),
            "sunset": greek.format_hhmm(_at(daily.get("sunset"), index)),
            "daylight": greek.format_duration(_at(daily.get("daylight_duration"), index)),
            "sunshine": greek.format_duration(_at(daily.get("sunshine_duration"), index)),
            "uv_max": uv_max,
            "radiation_sum": _at(daily.get("shortwave_radiation_sum"), index),
            "uv_level": uv_label,
            "uv_color": uv_colour,
            "wind_max": _at(daily.get("wind_speed_10m_max"), index),
            "gust_max": _at(daily.get("wind_gusts_10m_max"), index),
            "wind_dir_text": greek.compass(direction) if direction is not None else None,
            # How far apart the models are on this particular day.
            "agreement": (agreement or {}).get(str(stamp)),
        })
    return points


def _start_index(hourly, now_local):
    """Index of the hourly slot covering the current hour.

    Open-Meteo returns *local* wall-clock timestamps with no offset when a
    named timezone is requested, so the comparison happens in naive local time.
    """
    times = hourly.get("time") or []
    if not times:
        return 0
    floor = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    for index, stamp in enumerate(times):
        parsed = _parse_local(str(stamp), None)
        if parsed is not None and parsed >= floor:
            return index
    return max(0, len(times) - 1)


def _summary(current, today, alerts):
    """One short Greek sentence describing the moment."""
    head = "%s, %s°C" % (current["text"], greek.number(current["temp"]))
    parts = []
    if today:
        parts.append("Σήμερα %s° έως %s°" % (
            greek.number(today["min"]), greek.number(today["max"])))
        rain = today.get("precip_sum") or 0
        if rain >= 1:
            parts.append("αναμένεται βροχή %.1f mm" % rain)
        elif (today.get("precip_prob") or 0) >= 40:
            parts.append("πιθανή βροχή %d%%" % today["precip_prob"])
        else:
            parts.append("χωρίς αξιόλογη βροχή")
    if alerts:
        count = len(alerts)
        parts.append("%d ενεργή προειδοποίηση" % count if count == 1
                     else "%d ενεργές προειδοποιήσεις" % count)
    tail = ", ".join(parts)
    return head + (". " + tail + "." if tail else ".")


# WMO groups, by what they mean for a greeting or a clothing hint.
_CODES_SNOW = (71, 73, 75, 77, 85, 86)
_CODES_STORM = (95, 96, 99)
_CODES_RAIN = (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82)
_CODES_FOG = (45, 48)


def build_greeting(now_local, current):
    """A hello keyed to the hour, then to whatever the weather is doing.

    Deliberately says nothing about clothing: that is the outfit card's job,
    and repeating it here would just be noise.
    """
    code = current.get("code")
    feels = current.get("apparent")
    temp = current.get("temp")
    hour = now_local.hour

    if code in _CODES_SNOW:
        key = "snow"
    elif code in _CODES_STORM:
        key = "storm"
    elif code in _CODES_RAIN:
        key = "rain"
    elif code in _CODES_FOG:
        key = "fog"
    elif _is_number(feels) and feels <= 0:
        key = "frost"
    elif _is_number(feels) and feels <= 6:
        key = "cold"
    elif _is_number(temp) and temp >= 33:
        key = "heat"
    elif code == 0:
        key = "clear-night" if not current.get("is_day", 1) else "clear-day"
    else:
        key = "cloud"

    return {
        "word": greek.greeting_word(hour),
        "text": greek.GREETING_LINES.get(key, ""),
        "key": key,
    }


def build_outfit(hours, now_local):
    """What to wear across the day ahead.

    Three layers of advice, in the order a person asks for it:

    * a headline naming actual garments, from the feels-like temperature;
    * a detail line giving the range over the next twelve hours, because
      "12°" alone does not say whether that is the whole day;
    * a short list of extras — umbrella, sunscreen, ice — several of which
      can apply at once, which is why they are a list and not one alert.

    ``now`` is the top of the hour closest to the present, so the headline
    answers "what do I put on to go out", not "what was the average".
    """
    if not hours:
        return None
    now = hours[0]
    if greek.outfit_layer(now.get("apparent")) is None:
        return None

    ahead = hours[:12]
    feels = [h.get("apparent") for h in ahead if _is_number(h.get("apparent"))]
    span = (max(feels) - min(feels)) if len(feels) > 1 else 0.0
    low = min(feels) if feels else None
    high = max(feels) if feels else None

    # A wide swing makes the single layer answer wrong at one end or the other,
    # so both ends are named instead of hiding behind the word "layers".
    swing = None
    if span >= greek.OUTFIT_SWING and low is not None and high is not None:
        swing = greek.outfit_swing(low, high)
    if swing:
        key, headline, emoji = greek.OUTFIT_LAYERS
        headline = swing
    else:
        key, headline, emoji = greek.outfit_layer(now.get("apparent"))

    # Rain is judged over the hours you would actually be out in it.
    wet = max((h.get("precip_prob") or 0) for h in ahead[:6]) if ahead else 0
    gusts = now.get("gusts")
    wind_now = now.get("wind")
    strongest = max([v for v in (gusts, wind_now) if _is_number(v)] or [0])
    uv = now.get("uv")
    apparent = now.get("apparent")

    items = []
    if wet >= 50:
        # A brolly in a gale is worse than useless.
        items.append(greek.OUTFIT_RAINCOAT if gusts is not None and gusts >= 40
                     else greek.OUTFIT_UMBRELLA)
    elif wet >= 20:
        items.append(greek.OUTFIT_MAYBE_UMBRELLA)

    if strongest >= 50:
        items.append(greek.OUTFIT_WIND)

    if _is_number(uv) and uv >= 6:
        items.append(greek.OUTFIT_SUNSCREEN)
        if _is_number(apparent) and apparent >= 27:
            items.append(greek.OUTFIT_HAT)

    # Ice needs both a freezing surface and water about.
    if _is_number(apparent) and apparent <= 1 and wet >= 30:
        items.append(greek.OUTFIT_ICE)

    # The chip is only needed when the headline does not already say it.
    if span >= greek.OUTFIT_SWING and not swing:
        items.append(greek.OUTFIT_LAYERS)

    # One combined line, only when two things together say more than each does
    # alone. Otherwise the chips already cover it.
    advice = None
    if wet >= 50 and gusts is not None and gusts >= 40:
        advice = greek.OUTFIT_ADVICE["wind-and-rain"]
    elif span >= 15.0:
        advice = greek.OUTFIT_ADVICE["big-swing"]
    elif _is_number(apparent) and apparent <= -5:
        advice = greek.OUTFIT_ADVICE["freezing"]
    elif _is_number(apparent) and apparent >= 33:
        advice = greek.OUTFIT_ADVICE["scorching"]
    elif (strongest >= 40 and _is_number(apparent)
          and apparent <= now.get("temp", apparent) - 4):
        advice = greek.OUTFIT_ADVICE["wind-chill"]

    detail = "Αίσθηση %+.1f° τώρα" % apparent if _is_number(apparent) else ""
    if low is not None and high is not None and span >= 2.0:
        detail += (" · " if detail else "") + \
                  "τις επόμενες ώρες %.0f° έως %.0f°" % (low, high)

    return {
        "key": key,
        "text": headline,
        "emoji": emoji,
        "detail": detail,
        "advice": advice,
        "apparent": apparent,
        "temp": now.get("temp"),
        "feels_low": round(low, 1) if low is not None else None,
        "feels_high": round(high, 1) if high is not None else None,
        "span": round(span, 1),
        "rain_chance": round(wet) if _is_number(wet) else None,
        "wind": round(strongest, 1) if strongest else None,
        "uv": uv if _is_number(uv) else None,
        "items": [{"key": k, "text": t, "emoji": e} for k, t, e in items],
    }


def _moon_facts(snapshot, now_local):
    """Today's moon, or ``None`` when the API did not supply one."""
    daily = ((snapshot.get("forecast") or {}).get("daily")) or {}
    times = daily.get("time") or []
    if not times:
        return None
    try:
        index = times.index(now_local.date().isoformat())
    except ValueError:
        index = 0
    phase = _at(daily.get("moon_phase"), index)
    if phase is None:
        return None
    key, name, emoji = greek.moon_phase(phase)
    return {
        "phase": key,
        "name": name,
        "emoji": emoji,
        "illumination": greek.moon_illumination(phase),
        "rise": greek.format_hhmm(_at(daily.get("moonrise"), index)),
        "set": greek.format_hhmm(_at(daily.get("moonset"), index)),
    }


def build_sky(moon, hours, air):
    """The moon row, plus whether tonight is worth looking up.

    ``moon`` comes from :func:`_moon_facts`; it is passed in rather than read
    again here because the hero icon needs the same phase.
    """
    if not moon:
        return None

    # Tonight is the *remaining* dark hours, starting now. Taking the first
    # eight night hours from an 02:00 request mixed in the following evening,
    # so a 35% cloud figure was really tomorrow night's.
    night, started = [], False
    for hour in hours:
        if hour.get("is_day"):
            if started:
                break
            continue                    # still daytime: wait for sunset
        started = True
        night.append(hour)
        if len(night) >= 12:
            break
    clouds = [h.get("cloud") for h in night if _is_number(h.get("cloud"))]
    cover = round(sum(clouds) / len(clouds)) if clouds else None

    aqi = (air or {}).get("aqi")
    verdict = greek.stargazing_level(cover, aqi, moon["illumination"])
    if verdict is None:
        stargazing = None
    else:
        vkey, vtext, vcolour = verdict
        stargazing = {
            "level": vkey,
            "text": vtext,
            "color": vcolour,
            "cloud": cover,
            "aqi": aqi,
        }

    return {
        "phase": moon["phase"],
        "name": moon["name"],
        "emoji": moon["emoji"],
        "illumination": moon["illumination"],
        "rise": moon["rise"],
        "set": moon["set"],
        "stargazing": stargazing,
    }


# The ten-year baseline the "normal" card compares against is ERA5, and ERA5
# runs warm at Florina. This is by how much, month by month, measured against
# fourteen years of daily records from a station in the town itself: 5097 days,
# 2010-2023, every month except February, March and April too warm, up to
# 1.39 °C in August and 0.66 °C across the year.
#
# Applying it is calibration with evidence rather than a fudge — leaving it off
# made almost every day read colder relative to normal than it really was.
# Regenerate with the source below if the baseline period ever changes.
#
# Source: Εθνικό Αστεροσκοπείο Αθηνών / meteo.gr, "Ημερήσιες μετεωρολογικές
# παράμετροι για την περίοδο 2010-2023", data.gov.gr, CC BY 4.0.
ERA5_BIAS_MONTHLY = (
    0.0,                                    # index 0 is unused
    -0.97, 0.33, 0.35, 0.06, -0.40, -1.03,
    -1.31, -1.39, -1.04, -0.98, -0.63, -0.92,
)


def era5_bias(month):
    """Measured minus ERA5 for a month, or 0.0 outside 1-12."""
    try:
        month = int(month)
    except (TypeError, ValueError):
        return 0.0
    if 1 <= month <= 12:
        return ERA5_BIAS_MONTHLY[month]
    return 0.0


def build_normal(normals, today):
    """How today compares with the same date over the past decade.

    The archive API returns whole years, so the value for a given day is
    averaged across the years it covers rather than fetched per year.
    """
    daily = (normals or {}).get("daily") or {}
    times = daily.get("time") or []
    values = daily.get("temperature_2m_mean") or []
    if not times or not today:
        return None
    target = str(today.get("iso"))[5:]          # MM-DD
    if not target:
        return None

    seen = []
    for stamp, value in zip(times, values):
        if str(stamp)[5:] == target and value is not None:
            seen.append(value)
    if len(seen) < 3:                            # too few years to call it normal
        return None

    try:
        month = int(target[:2])
    except ValueError:
        month = 0
    bias = era5_bias(month)
    mean = sum(seen) / len(seen) + bias

    today_mean = today.get("mean")
    if today_mean is None:
        low, high = today.get("min"), today.get("max")
        if low is None or high is None:
            return None
        today_mean = (low + high) / 2.0

    delta = today_mean - mean
    return {
        "value": round(mean, 1),
        "today": round(today_mean, 1),
        "delta": round(delta, 1),
        "bias": round(bias, 2),
        "years": len(seen),
        "range": [round(min(seen) + bias, 1), round(max(seen) + bias, 1)],
        "warmer": delta >= 0,
        "text": greek.normal_text(delta),
    }


def build_snow(snapshot, config, now_local=None):
    """Snow at the ski area, when there is any worth reporting.

    Returns ``None`` out of season: a row of zeros all summer is noise.
    """
    payload = snapshot.get("snow")
    if not isinstance(payload, list):
        payload = [payload] if isinstance(payload, dict) else []
    points = []
    hours = []
    for index, place in enumerate(config.snow_points):
        block = payload[index] if index < len(payload) else None
        if not block:
            continue
        daily = block.get("daily") or {}
        times = daily.get("time") or []
        if not times:
            continue

        falls = [v for v in (daily.get("snowfall_sum") or []) if _is_number(v)]
        depths = [v for v in (daily.get("snow_depth_max") or []) if _is_number(v)]
        lows = [v for v in (daily.get("temperature_2m_min") or []) if _is_number(v)]
        if not falls and not depths:
            continue

        total_fall = sum(falls) if falls else 0.0
        deepest = max(depths) if depths else 0.0
        # snow_depth is metres of water equivalent, snowfall is centimetres.
        depth_cm = deepest * 100.0
        # No "nothing to say" gate here any more. Snow used to be its own card
        # and hid itself out of season; the mountain card needs the same
        # forecast in July, when the temperature gap is the point and the snow
        # depth is legitimately zero.

        points.append({
            "key": place.get("key") or str(index),
            "name": place.get("name") or "?",
            "elevation": round(block.get("elevation") or 0),
            "fall": round(total_fall, 1),
            "depth": round(depth_cm, 1),
            "min": round(min(lows), 1) if lows else None,
            "days": len([v for v in falls if v >= 1.0]),
            "level": "snow" if (total_fall >= 1.0 or depth_cm >= 2.0) else "none",
        })
    if not points:
        return None
    points.sort(key=lambda p: -p["elevation"])

    # The hours come from the first location, which is the highest — the pass
    # the road status is about.
    top = payload[0] if payload else {}
    top_hourly = (top or {}).get("hourly") or {}
    stamps = top_hourly.get("time") or []
    temps = top_hourly.get("temperature_2m") or []
    precip = top_hourly.get("precipitation") or []
    levels = top_hourly.get("freezing_level_height") or []
    winds = top_hourly.get("wind_speed_10m") or []
    gusts = top_hourly.get("wind_gusts_10m") or []

    # Sliced from the current hour forward, not from midnight. The road status
    # is about what is coming, and counting the morning's snow towards this
    # evening's risk made a clearing day look like a blizzard.
    start = 0
    if now_local is not None:
        key = now_local.strftime("%Y-%m-%dT%H")
        for index, stamp in enumerate(stamps):
            if str(stamp)[:13] >= key:
                start = index
                break
        else:
            start = max(0, len(stamps) - 36)

    for index in range(start, min(start + 36, len(stamps))):
        hours.append({
            "iso": str(stamps[index]),
            "time": str(stamps[index])[11:16],
            "now": index == start,
            "temp": _at(temps, index),
            "precip": _at(precip, index, 0.0),
            "freezing": _at(levels, index),
            "wind": _at(winds, index),
        })

    return {
        "points": points,
        "hours": hours,
        "deepest": max(p["depth"] for p in points),
        "fall": round(sum(p["fall"] for p in points), 1),
    }


def _station_time(stamp, tz=None):
    """Parse an EMY station stamp, ``YYYYMMDDHHMM``, as **UTC**.

    It carries no separators and no zone, and it is UTC rather than the wall
    clock. Two things establish that. EMY's own portal prints the same
    timestamps and its tooltips carry the raw AUTO report ending in ``Z``; and
    putting the station's diurnal cycle against a local-time model cycle lines
    them up only after a three-hour shift, at r = 0.99 against 0.96 unshifted.

    Reading them as local made every station look three hours older than it
    was, and printed the wrong observation time.
    """
    text = str(stamp or "").strip()
    if len(text) != 12 or not text.isdigit():
        return None
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M")
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=timezone.utc)
    if tz is not None:
        return parsed.astimezone(tz).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def _reading(value):
    """One EMY station value, or ``None``.

    Everything arrives as a string and missing readings are a run of slashes
    of varying length — ``/``, ``///``, ``/////`` are all "no sensor", not
    numbers, and not zero.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or "/" in text or text.upper() in ("NAN", "NULL", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _station_reading(entry, now_local, tz=None):
    """One station's latest observation, or ``None`` if it is too stale.

    This is the only number on the page that is measured rather than modelled,
    which is why it is shown next to the forecast. It is also not Florina:
    there is no EMY station in the town, so each card names its own station.

    Freshness varies wildly across the network — on the same day Kastoria ran
    about five hours behind and Chortiatis a month — so each station carries
    its own tolerance rather than sharing one.
    """
    records = entry.get("records") or []
    if not records:
        return None
    record = records[0]
    observed = _station_time(record.get("yyyyMMddHHmm"), tz)
    if observed is None:
        return None

    age = (now_local.replace(tzinfo=None) - observed).total_seconds()
    limit = entry.get("max_age_hours")
    limit = float(limit) * 3600.0 if limit else 12 * 3600.0
    if age < -3600 or age > limit:
        return None                     # a month-old reading is not a reading

    temperature = _reading(record.get("Temp_Dry_5min"))
    if temperature is None:
        return None

    # Everything since midnight, from the same window of records, so the card
    # can give the station's own day rather than only an instant.
    today = observed.date()
    same_day = []
    rain_total = 0.0
    rain_seen = False
    for item in records:
        stamp = _station_time(item.get("yyyyMMddHHmm"), tz)
        if stamp is None or stamp.date() != today:
            continue
        same_day.append(item)
        drop = _reading(item.get("Prec_Sum_1_5min"))
        if drop is not None:
            rain_seen = True
            rain_total += drop

    day_temps = []
    for item in same_day:
        for key in ("Temp_Dry_5min", "Temp_Dry_Min_5min", "Temp_Dry_Max_5min"):
            value = _reading(item.get(key))
            if value is not None:
                day_temps.append(value)

    # A 5-minute extreme, not the instant: "the coldest it got in the last
    # five minutes" is calmer than a single sample.
    low = _reading(record.get("Temp_Dry_Min_5min"))
    high = _reading(record.get("Temp_Dry_Max_5min"))
    humidity = _reading(record.get("Rel_Hum_5min"))
    wind = _reading(record.get("Wind_Speed_Avg_5min"))
    gusts = _reading(record.get("Wind_Speed_Max_5min"))
    direction = _reading(record.get("Wind_Dir_Avg_5min"))
    rain = _reading(record.get("Prec_Sum_1_5min"))
    pressure = _reading(record.get("Press_Barometer_5min"))
    radiation = _reading(record.get("Rad_Global_5min"))

    return {
        "id": entry.get("id"),
        "name": entry.get("name") or entry.get("id"),
        "distance": entry.get("distance") or "",
        "primary": bool(entry.get("primary")),
        "observed": observed.strftime("%H:%M"),
        "age_minutes": int(round(age / 60.0)),
        "age_text": greek.format_duration(max(0.0, age)),
        "temp": round(temperature, 1),
        "min": round(low, 1) if low is not None else None,
        "max": round(high, 1) if high is not None else None,
        "day_min": round(min(day_temps), 1) if day_temps else None,
        "day_max": round(max(day_temps), 1) if day_temps else None,
        "samples": len(same_day),
        "humidity": round(humidity) if humidity is not None else None,
        "wind": round(wind, 1) if wind is not None else None,
        "gusts": round(gusts, 1) if gusts is not None else None,
        "wind_dir_text": greek.compass(direction) if direction is not None else None,
        "rain": round(rain, 1) if rain is not None else None,
        "rain_today": round(rain_total, 1) if rain_seen else None,
        "pressure": round(pressure, 1) if pressure is not None else None,
        "radiation": round(radiation, 1) if radiation is not None else None,
    }


_EMY_AUTO = re.compile(
    r"\b(?P<station>\d{3})\s+"
    r"(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z\s+"
    r"(?:AUTO|COR|NIL)?\s*"
    r"(?P<wind>\S+?)\s+"
    r".*?"
    r"(?P<temp>M?\d{2})/(?P<dew>M?\d{2})\s+"
    r"Q(?P<pressure>\d{4})"
)


def _auto_value(text):
    """An AUTO temperature: ``M03`` is minus three, not a string."""
    if not text:
        return None
    negative = text.startswith("M")
    try:
        value = float(text[1:] if negative else text)
    except ValueError:
        return None
    return -value if negative else value


def _auto_wind(token):
    """``VRB02KT`` or ``24005KT`` -> (degrees or None, km/h).

    The direction is three digits and the speed everything after them, so
    slicing fixed offsets breaks on any speed that is not two digits.
    """
    if not token:
        return None, None
    text = str(token).strip().upper()

    factor = 1.852                       # knots -> km/h
    if text.endswith("MPS"):
        text, factor = text[:-3], 3.6    # metres per second -> km/h
    elif text.endswith("KT"):
        text = text[:-2]
    elif text.endswith("KMH"):
        text, factor = text[:-3], 1.0

    direction = None
    if text.startswith("VRB"):
        digits = text[3:]
    elif len(text) > 3:
        try:
            direction = float(text[:3])
        except ValueError:
            direction = None
        digits = text[3:]
    else:
        digits = text

    try:
        speed = float(digits)
    except ValueError:
        return direction, None
    if direction is not None and not 0 <= direction <= 360:
        direction = None                 # a bogus bearing is worse than none
    return direction, round(speed * factor, 1)


def _humidity_from_dew(temp, dew):
    """Relative humidity from the dew point, Magnus form.

    Computed rather than scraped: it is physics, and the humidity column is
    exactly the sort of markup that changes.
    """
    if temp is None or dew is None:
        return None
    try:
        def saturation(value):
            return math.exp((17.625 * value) / (243.04 + value))
        return max(1.0, min(100.0, 100.0 * saturation(dew) / saturation(temp)))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def parse_florina(payload, now_local, config, tz=None):
    """The live reading from EMY's station in the town.

    The page carries roughly a day of half-hourly AUTO reports. They are UTC
    and name no month, so the stamp is resolved against the local clock: the
    candidate nearest now wins, which handles both a month boundary and the
    few hours of lag.

    This is the one source that is HTML, and it is deliberately the only one
    parsed from a machine-formatted string rather than from markup.
    """
    html = (payload or {}).get("html")
    if not html or not getattr(config, "florina_enabled", True):
        return None

    # The zone comes in resolved: a stock Windows Python has no tzdata, so
    # `_zone` returns None there and only the caller knows the fallback.
    tz = tz or _zone(getattr(config, "timezone", "Europe/Athens")) or timezone.utc
    base = now_local.replace(tzinfo=None)

    readings = []
    for match in _EMY_AUTO.finditer(html):
        fields = match.groupdict()
        temp = _auto_value(fields.get("temp"))
        dew = _auto_value(fields.get("dew"))
        if temp is None:
            continue
        try:
            day = int(fields["day"])
            hour = int(fields["hour"])
            minute = int(fields["minute"])
            pressure = int(fields["pressure"])
        except (KeyError, TypeError, ValueError):
            continue

        # No month in the report, so try the neighbours and keep the closest.
        best = None
        for offset in (-1, 0, 1):
            year, month = _shift_month(base.year, base.month, offset)
            try:
                stamp = datetime(year, month, day, hour, minute,
                                 tzinfo=timezone.utc)
            except ValueError:
                continue
            local = stamp.astimezone(tz).replace(tzinfo=None)
            gap = abs((local - base).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, local, stamp)
        if best is None:
            continue

        direction, speed = _auto_wind(fields.get("wind"))
        readings.append({
            "when": best[1],
            "age": (base - best[1]).total_seconds(),
            "temp": temp,
            "dew": dew,
            "humidity": _humidity_from_dew(temp, dew),
            "pressure": float(pressure),
            "wind": speed,
            "wind_dir": direction,
        })

    if not readings:
        return None
    readings.sort(key=lambda r: r["when"])
    latest = readings[-1]

    limit = float(getattr(config, "florina_max_age", 3 * 3600) or 3 * 3600)
    if latest["age"] < -1800 or latest["age"] > limit:
        return None

    return {
        "id": "16613",
        "name": getattr(config, "florina_name", "Φλώρινα"),
        "distance": getattr(config, "florina_site", "μέσα στην πόλη"),
        "primary": True,
        "observed": latest["when"].strftime("%H:%M"),
        "age_minutes": int(round(max(0.0, latest["age"]) / 60.0)),
        "age_text": greek.format_duration(max(0.0, latest["age"])),
        "temp": round(latest["temp"], 1),
        "min": None,                  # the page carries hours, not a full day
        "max": None,
        "day_min": None,
        "day_max": None,
        "samples": len(readings),
        "humidity": (round(latest["humidity"])
                     if latest["humidity"] is not None else None),
        "wind": latest["wind"],
        "gusts": None,
        "wind_dir_text": (greek.compass(latest["wind_dir"])
                          if latest["wind_dir"] is not None else None),
        "rain": None,
        "rain_today": None,
        "pressure": round(latest["pressure"], 1),
        "radiation": None,
    }


def _shift_month(year, month, offset):
    index = (year * 12 + (month - 1)) + offset
    return index // 12, index % 12 + 1


def build_stations(snapshot, config, now_local, tz=None):
    """The town station if EMY is answering, plus the open-data references.

    The card shows one station and the strip at the bottom shows the rest. The
    town's own reading leads when it is available, because a proxy 30 km away
    is a worse answer to "what is it doing outside" than the town itself.
    """
    entries = snapshot.get("station")
    references = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            reading = _station_reading(entry, now_local, tz)
            if reading:
                references.append(reading)

    florina = parse_florina(snapshot.get("florina"), now_local, config,
                             tz=tz)
    if florina:
        # The town leads; anything else becomes a reference beside it.
        for reading in references:
            reading["primary"] = False
        return {"primary": florina, "all": [florina] + references,
                "town": True}

    if not references:
        return None
    primaries = [r for r in references if r["primary"]]
    return {
        "primary": primaries[0] if primaries else references[0],
        "all": references,
        "town": False,
    }


def build_comfort(hours):
    """The dew point, which is what "muggy" actually means.

    Relative humidity alone misleads: 60% at 5 °C is a dry day and 60% at 25 °C
    is sticky. The dew point is the same number in both, so it is the honest
    measure of how heavy the air feels.
    """
    if not hours:
        return None
    now = hours[0]
    dew = now.get("dew_point")
    if not _is_number(dew):
        return None

    ahead = [h.get("dew_point") for h in hours[:12]
             if _is_number(h.get("dew_point"))]
    low = min(ahead) if ahead else dew
    high = max(ahead) if ahead else dew
    key, label, colour = greek.comfort_level(dew)
    return {
        "dew_point": round(dew, 1),
        "low": round(low, 1),
        "high": round(high, 1),
        "humidity": now.get("humidity"),
        "level": key,
        "label": label,
        "color": colour,
        "spread": round(high - low, 1),
    }


def build_solar(today, normals):
    """Today's solar energy, against what the date normally delivers.

    Both numbers are the day's total in MJ/m² — the forecast gives one and the
    archive gives the other — so the comparison needs no conversion and reads
    the same at 3am as at noon. Summing the *remaining* hours instead made
    every evening look like heavy cloud.
    """
    if not today:
        return None
    energy = today.get("radiation_sum")
    if not _is_number(energy):
        return None

    daily = (normals or {}).get("daily") or {}
    times = daily.get("time") or []
    values = daily.get("shortwave_radiation_sum") or []
    target = str(today.get("iso") or "")[5:]        # MM-DD
    if not target:
        return None
    history = []
    for stamp, value in zip(times, values):
        if str(stamp)[5:] == target and _is_number(value):
            history.append(value)
    if len(history) < 3:
        return None

    expected = sum(history) / len(history)
    if expected <= 0:
        return None
    share = max(0.0, min(140.0, energy / expected * 100.0))
    key, label, colour = greek.solar_level(share)
    return {
        # 1 MJ/m² = 0.2778 kWh/m², the unit a panel owner thinks in.
        "energy": round(energy * 0.2778, 2),
        "expected": round(expected * 0.2778, 2),
        "share": round(share),
        "years": len(history),
        "level": key,
        "label": label,
        "color": colour,
        "text": greek.solar_text(share),
    }


def build_road(snow, config, now_local):
    """Whether the Vigla pass is likely to need chains.

    The freezing level decides it, not the valley temperature: the pass sits at
    about 1773 m, so rain in town can be snow up there with nobody the wiser.
    A forecast of precipitation with the freezing level at or below the pass is
    the signal — the same logic as the frost card, one altitude up.
    """
    points = (snow or {}).get("points") or []
    if not points:
        return None
    top = points[0]                           # highest first, built that way
    elevation = top.get("elevation") or 0
    if not elevation:
        return None

    risk = 0
    first = None
    coldest = None
    lowest = None
    for hour in (snow or {}).get("hours") or []:
        wet = _is_number(hour.get("precip")) and hour["precip"] >= 0.2
        temp = hour.get("temp")
        frozen = _is_number(temp) and temp <= 1.0
        level = hour.get("freezing")
        if _is_number(level):
            lowest = level if lowest is None else min(lowest, level)
            if level <= elevation:
                frozen = True
        if _is_number(temp):
            coldest = temp if coldest is None else min(coldest, temp)
        if wet and frozen:
            risk += 1
            if first is None:
                first = hour

    fall = top.get("fall") or 0.0
    depth = top.get("depth") or 0.0
    if risk == 0 and fall < 1.0 and depth < 2.0:
        return None

    key, label, colour = greek.road_level(risk, fall, depth)
    return {
        "name": config.snow_points[0].get("name") or "Βίγλα",
        "elevation": round(elevation),
        "level": key,
        "label": label,
        "color": colour,
        "risk_hours": risk,
        "fall": round(fall, 1),
        "depth": round(depth, 1),
        "coldest": round(coldest, 1) if coldest is not None else None,
        "freezing": round(lowest) if lowest is not None else None,
        "first": first,
        "text": greek.road_text(key, risk),
    }


def build_mountain(snow, road, current, config, now_local):
    """One card for Vitsi / Pisoderi, which changes with the season.

    Winter asks whether the road is passable and whether there is snow; summer
    asks whether it is worth driving up to escape the heat. Both read the same
    forecast at the same point, so they share a card and cost nothing extra.

    The forecast is the only option for current conditions here. EMY's station
    on the ridge is real and sits at roughly the right height, but its feed
    arrives in batches running from hours to more than a day behind, so it is
    an archive rather than a live reading — see DESIGN.md.
    """
    if not snow or not snow.get("points"):
        return None
    points = snow["points"]
    top = points[0] if points else None
    if not top:
        return None

    month = getattr(now_local, "month", None)
    depth = top.get("depth") or 0.0
    fall = top.get("fall") or 0.0
    season = greek.mountain_season(month, depth, fall)
    label, emoji = greek.mountain_label(season)

    hours = snow.get("hours") or []
    now_hour = None
    for hour in hours:
        if hour.get("now"):
            now_hour = hour
            break
    if now_hour is None and hours:
        now_hour = hours[0]

    mountain_temp = now_hour.get("temp") if now_hour else None
    mountain_wind = None
    if now_hour is not None and _is_number(now_hour.get("wind")):
        mountain_wind = now_hour["wind"]

    city_temp = (current or {}).get("temp")
    gap = None
    if _is_number(city_temp) and _is_number(mountain_temp):
        gap = round(city_temp - mountain_temp, 1)

    road_key = (road or {}).get("level") or "none"
    if season == "winter":
        hint = greek.winter_hint(road_key, mountain_temp, mountain_wind, depth)
    else:
        hint = greek.summer_hint(gap, mountain_temp, mountain_wind)

    return {
        "name": "Βίτσι / Πισοδέρι",
        "season": season,
        "label": label,
        "emoji": emoji,
        "elevation": top.get("elevation"),
        "temp": (round(mountain_temp, 1) if _is_number(mountain_temp) else None),
        "city_temp": (round(city_temp, 1) if _is_number(city_temp) else None),
        "wind": mountain_wind,
        "gap": gap,
        "gap_text": greek.mountain_gap_text(gap) if gap is not None else "",
        "depth": round(depth, 1),
        "fall": round(float(fall), 1),
        "road": road,
        "road_key": road_key,
        "hint": hint,
    }


def build_report(snapshot, config, now=None):
    """Assemble the JSON document served at ``/api/weather``."""
    forecast = snapshot.get("forecast")
    if not isinstance(forecast, dict) or "current" not in forecast:
        raise ReportError("no forecast data available")
    for key in ("hourly", "daily"):
        if not isinstance(forecast.get(key), dict) or not forecast[key].get("time"):
            raise ReportError("forecast payload is missing '%s'" % key)

    tz, tz_source = resolve_timezone(config.timezone, forecast)
    now_local = now or datetime.now(tz)
    if now_local.tzinfo is None:
        # Callers (and tests) may hand us a naive wall clock; treat it as local.
        now_local = now_local.replace(tzinfo=tz)
    else:
        now_local = now_local.astimezone(tz)

    hourly_raw = forecast["hourly"]
    daily_raw = forecast["daily"]
    current_raw = forecast["current"]

    moon = _moon_facts(snapshot, now_local)
    start = _start_index(hourly_raw, now_local)
    hours = _hourly_points(hourly_raw, start, max(1, config.forecast_hours), tz, moon)
    agreement = build_agreement(snapshot.get("terrain"), config)
    days = _daily_points(daily_raw, tz, now_local.date(), config, agreement,
                         hourly_raw)

    visibility = hours[0]["visibility"] if hours else None
    code = current_raw.get("weather_code", 0)
    is_day = bool(current_raw.get("is_day", 1))
    direction = current_raw.get("wind_direction_10m")
    speed = current_raw.get("wind_speed_10m")
    beaufort = greek.beaufort(speed)

    # On a clear night the hero shows the real moon, not a stock crescent: a
    # full moon drawn as a sliver, two panels above a card reading Πανσέληνος,
    # is the page contradicting itself.
    icon = greek.emoji(code, is_day)
    if moon and not is_day and code in (0, 1):
        icon = moon["emoji"]

    current = {
        "iso": current_raw.get("time"),
        "time": greek.format_hhmm(str(current_raw.get("time") or "")),
        "date_text": greek.long_date(now_local),
        "temp": current_raw.get("temperature_2m"),
        "apparent": current_raw.get("apparent_temperature"),
        "humidity": current_raw.get("relative_humidity_2m"),
        "precip": current_raw.get("precipitation"),
        "rain": current_raw.get("rain"),
        "showers": current_raw.get("showers"),
        "snowfall": current_raw.get("snowfall"),
        "code": code,
        "text": greek.describe(code),
        "emoji": icon,
        "is_day": is_day,
        "cloud": current_raw.get("cloud_cover"),
        "pressure": current_raw.get("pressure_msl"),
        "wind": speed,
        "gusts": current_raw.get("wind_gusts_10m"),
        "wind_dir": direction,
        "wind_dir_text": greek.compass(direction) if direction is not None else None,
        "wind_arrow": greek.wind_arrow(direction) if direction is not None else None,
        "beaufort": beaufort,
        "beaufort_text": greek.beaufort_text(beaufort),
        "visibility": visibility,
        "visibility_text": greek.visibility_text(visibility),
    }

    today = days[0] if days else None
    alerts = parse_alerts(snapshot.get("alerts"), config, now_local)

    # The strip's first card is labelled «τώρα», so it must agree with the hero.
    # `current` is a 15-minute interpolated reading while `hourly[0]` is the top
    # of the hour, and the two genuinely disagree: they were showing different
    # weather codes for the same moment.
    if hours and current:
        for field in ("temp", "apparent", "humidity", "precip", "code", "text",
                      "emoji", "is_day", "cloud", "wind", "gusts",
                      "wind_dir", "wind_dir_text", "wind_arrow", "beaufort"):
            if current.get(field) is not None:
                hours[0][field] = current[field]

    # Built once and shared: the road status needs the same forecast the snow
    # card reads, and fetching those two points twice would be wasteful.
    snow = build_snow(snapshot, config, now_local)

    local = build_local_conditions(snapshot, days, hourly_raw, now_local, config,
                                   snow)
    greeting = build_greeting(now_local, current)
    outfit = build_outfit(hours, now_local)
    air = parse_air(snapshot.get("air"))
    sky = build_sky(moon, hours, air)
    normal = build_normal(snapshot.get("normals"), today)
    station = build_stations(snapshot, config, now_local, tz=tz)
    comfort = build_comfort(hours)
    solar = build_solar(today, snapshot.get("normals"))
    road = build_road(snow, config, now_local)
    mountain = build_mountain(snow, road, current, config, now_local)

    ages = snapshot.get("ages") or {}
    errors = snapshot.get("errors") or {}
    forecast_age = ages.get("forecast")

    return {
        "place": config.place,
        "region": config.region,
        "timezone": config.timezone,
        "generated_at": now_local.isoformat(),
        "summary": _summary(current, today, alerts),
        "current": current,
        "today": today,
        "hourly": hours,
        "daily": days,
        "local": local,
        "greeting": greeting,
        "outfit": outfit,
        "sky": sky,
        "normal": normal,
        "station": station,
        "comfort": comfort,
        "solar": solar,
        "mountain": mountain,
        "air": air,
        "alerts": alerts,
        "status": {
            "stale": bool(snapshot.get("stale")),
            "age": round(forecast_age, 1) if forecast_age is not None else None,
            "errors": errors,
            "degraded": sorted(errors.keys()),
            "refresh": config.refresh,
            "timezone_source": tz_source,
        },
    }
