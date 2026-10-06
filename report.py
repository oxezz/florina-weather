# -*- coding: utf-8 -*-
"""Turns raw upstream payloads into the view model the front-end consumes.

Pure and deterministic: every function takes the data it needs (including
"now") as an argument, so the tests can pin a fake clock and synthetic
payloads and check the output exactly.
"""

from __future__ import annotations

import html
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
        worst = max(highs) - min(highs)
        if len(lows) > 1:
            worst = max(worst, max(lows) - min(lows))
        key, label, colour = greek.agreement_level(worst)
        spread[str(stamp)] = {
            "spread": round(worst, 1),
            "level": key,
            "label": label,
            "color": colour,
            "models": len(highs),
            "range": [round(min(highs), 1), round(max(highs), 1)],
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


def build_local_conditions(snapshot, days, forecast_hourly, now_local, config):
    """Assemble the hyper-local block, dropping anything with nothing to say."""
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


def _hourly_points(hourly, start_index, count, tz):
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
            "emoji": greek.emoji(code, is_day),
            "is_day": is_day,
            "wind": _at(hourly.get("wind_speed_10m"), index),
            "gusts": _at(hourly.get("wind_gusts_10m"), index),
            "wind_dir": direction,
            "wind_dir_text": greek.compass(direction) if direction is not None else None,
            "wind_arrow": greek.wind_arrow(direction) if direction is not None else None,
            "uv": _at(hourly.get("uv_index"), index),
            "cloud": _at(hourly.get("cloud_cover"), index),
            "visibility": _at(hourly.get("visibility"), index),
            "visibility_text": greek.visibility_text(_at(hourly.get("visibility"), index)),
            "day": parsed.date().isoformat() if parsed else None,
        })
    return points


def _daily_points(daily, tz, today, config, agreement=None):
    times = daily.get("time") or []
    points = []
    for index, stamp in enumerate(times):
        try:
            day = date.fromisoformat(str(stamp))
        except ValueError:
            continue
        code = _at(daily.get("weather_code"), index, 0)
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
    """What to wear, from the feels-like temperature and the hours ahead."""
    if not hours:
        return None
    now = hours[0]
    layer = greek.outfit_layer(now.get("apparent"))
    if layer is None:
        return None
    key, headline, emoji = layer

    items = []

    # Rain within the next six hours is worth an umbrella; further out is not
    # a decision anyone is making right now.
    ahead = hours[:6]
    wet = max((h.get("precip_prob") or 0) for h in ahead) if ahead else 0
    if wet >= 50:
        items.append(greek.OUTFIT_UMBRELLA)

    uv = now.get("uv")
    if _is_number(uv) and uv >= 6:
        items.append(greek.OUTFIT_SUNSCREEN)
        if _is_number(now.get("apparent")) and now["apparent"] >= 27:
            items.append(greek.OUTFIT_HAT)

    gusts = now.get("gusts")
    if _is_number(gusts) and gusts >= 50:
        items.append(greek.OUTFIT_WIND)

    # Ice needs both a freezing surface and water about.
    if _is_number(now.get("apparent")) and now["apparent"] <= 1 and wet >= 30:
        items.append(greek.OUTFIT_ICE)

    return {
        "key": key,
        "text": headline,
        "emoji": emoji,
        "apparent": now.get("apparent"),
        "rain_chance": round(wet) if _is_number(wet) else None,
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

    # Tonight is the dark stretch after sunset. Cloud and haze are read from
    # the same hours the observing would actually happen in.
    night = [h for h in hours if not h.get("is_day")][:8]
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

    start = _start_index(hourly_raw, now_local)
    hours = _hourly_points(hourly_raw, start, max(1, config.forecast_hours), tz)
    agreement = build_agreement(snapshot.get("terrain"), config)
    days = _daily_points(daily_raw, tz, now_local.date(), config, agreement)

    visibility = hours[0]["visibility"] if hours else None
    code = current_raw.get("weather_code", 0)
    is_day = bool(current_raw.get("is_day", 1))
    direction = current_raw.get("wind_direction_10m")
    speed = current_raw.get("wind_speed_10m")
    beaufort = greek.beaufort(speed)
    moon = _moon_facts(snapshot, now_local)

    # On a clear night the hero shows the real moon, not a stock crescent:
    # a full moon drawn as a sliver, two panels above a card reading
    # Πανσέληνος, is the page contradicting itself.
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
    local = build_local_conditions(snapshot, days, hourly_raw, now_local, config)
    greeting = build_greeting(now_local, current)
    outfit = build_outfit(hours, now_local)
    air = parse_air(snapshot.get("air"))
    sky = build_sky(moon, hours, air)

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
