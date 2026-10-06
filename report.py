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


def _air_hourly(air):
    """The hourly block of an air-quality payload, if there is one."""
    if not isinstance(air, dict):
        return None
    hourly = air.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        return None
    return hourly


def build_frost(days):
    """Frost risk across the forecast window, for growers.

    Returns ``None`` when no night in range gets near freezing, which keeps the
    card off the page from late spring to early autumn.
    """
    nights = []
    for day in days or []:
        level = greek.frost_level(day.get("min"))
        if level is None or day.get("min") is None:
            continue
        nights.append({
            "label": day["label"],
            "iso": day["iso"],
            "weekday": day["weekday"],
            "min": day["min"],
            "level": level[0],
        })
    if not nights:
        return None

    coldest = min(nights, key=lambda night: night["min"])
    key, label, colour = greek.frost_level(coldest["min"])
    frosty = [night for night in nights if night["min"] <= 0]
    return {
        "level": key,
        "label": label,
        "color": colour,
        "lowest": coldest["min"],
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
    cleanest_at, cleanest_value = min(whole_day, key=lambda item: item[1])

    key, label, colour = level
    return {
        "level": key,
        "label": label,
        "color": colour,
        "peak": round(peak_value, 1),
        "peak_time": peak_at.strftime("%H:%M"),
        "wind": round(mean_wind, 1) if mean_wind is not None else None,
        "calm": mean_wind is not None and mean_wind < SMOG_CALM_KMH,
        "cleanest_time": cleanest_at.strftime("%H:%M"),
        "cleanest": round(cleanest_value, 1),
    }


def build_local_conditions(snapshot, days, forecast_hourly, now_local):
    """Assemble the hyper-local block, dropping anything with nothing to say."""
    blocks = {
        "frost": build_frost(days),
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


def _daily_points(daily, tz, today, config):
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
    days = _daily_points(daily_raw, tz, now_local.date(), config)

    visibility = hours[0]["visibility"] if hours else None
    code = current_raw.get("weather_code", 0)
    is_day = bool(current_raw.get("is_day", 1))
    direction = current_raw.get("wind_direction_10m")
    speed = current_raw.get("wind_speed_10m")
    beaufort = greek.beaufort(speed)

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
        "emoji": greek.emoji(code, is_day),
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
    local = build_local_conditions(snapshot, days, hourly_raw, now_local)

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
        "air": parse_air(snapshot.get("air")),
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
