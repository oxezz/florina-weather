# -*- coding: utf-8 -*-
"""Synthetic upstream payloads, shaped exactly like the real ones.

Kept in one place so the report and server tests exercise the same fixtures.
"""

import datetime
import json

START = datetime.datetime(2026, 10, 6, 0, 0)
DAYS = 7
HOURS = DAYS * 24


def forecast(days=DAYS, start=START, code=2):
    """A complete Open-Meteo ``/v1/forecast`` response."""
    times = [(start + datetime.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
             for i in range(days * 24)]

    def hour_of(index):
        return (start + datetime.timedelta(hours=index)).hour

    temperature = [round(9 + 9 * (1 - abs(hour_of(i) - 15) / 15.0), 1)
                   for i in range(len(times))]
    # Clear most of the time, a little cloud in the early afternoon.
    codes = [2 if 10 <= hour_of(i) <= 14 else 0 for i in range(len(times))]

    daily_dates = [(start + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
                   for d in range(days)]

    return {
        "latitude": 40.78,
        "longitude": 21.41,
        "timezone": "Europe/Athens",
        "timezone_abbreviation": "EEST",
        "utc_offset_seconds": 10800,
        "current": {
            "time": "2026-10-06T22:00",
            "interval": 900,
            "temperature_2m": 11.1,
            "relative_humidity_2m": 49,
            "apparent_temperature": 8.0,
            "is_day": 0,
            "precipitation": 0.0,
            "rain": 0.0,
            "showers": 0.0,
            "snowfall": 0.0,
            "weather_code": 0,
            "cloud_cover": 3,
            "pressure_msl": 1022.5,
            "surface_pressure": 944.9,
            "wind_speed_10m": 7.9,
            "wind_direction_10m": 231,
            "wind_gusts_10m": 16.2,
        },
        "hourly": {
            "time": times,
            "temperature_2m": temperature,
            "apparent_temperature": [round(t - 3, 1) for t in temperature],
            "relative_humidity_2m": [50 for _ in times],
            "precipitation_probability": [20 if hour_of(i) % 5 == 0 else 5
                                          for i in range(len(times))],
            "precipitation": [0.4 if hour_of(i) == 6 else 0.0 for i in range(len(times))],
            "weather_code": codes,
            "is_day": [1 if 7 <= hour_of(i) <= 18 else 0 for i in range(len(times))],
            "wind_speed_10m": [7.9 for _ in times],
            "wind_gusts_10m": [16.2 for _ in times],
            "wind_direction_10m": [231 for _ in times],
            "uv_index": [max(0.0, round(6 - abs(hour_of(i) - 13) * 0.8, 1))
                         for i in range(len(times))],
            "cloud_cover": [3 for _ in times],
            "visibility": [41220.0 for _ in times],
        },
        "daily": {
            "time": daily_dates,
            "weather_code": [code for _ in daily_dates],
            "temperature_2m_max": [22.4 + d for d in range(days)],
            "temperature_2m_min": [6.4 + d * 0.5 for d in range(days)],
            "apparent_temperature_max": [19.9 + d for d in range(days)],
            "apparent_temperature_min": [3.8 + d * 0.5 for d in range(days)],
            "precipitation_sum": [0.0, 3.2, 0.0, 0.0, 11.0, 0.0, 0.0][:days],
            "precipitation_probability_max": [0, 65, 10, 5, 90, 20, 15][:days],
            "sunrise": ["%sT07:%02d" % (d, 35 + i) for i, d in enumerate(daily_dates)],
            "sunset": ["%sT19:%02d" % (d, 8 - i) for i, d in enumerate(daily_dates)],
            "daylight_duration": [41571.26 - i * 120 for i in range(days)],
            "sunshine_duration": [39600.0 for _ in range(days)],
            "uv_index_max": [5.35, 4.95, 5.3, 3.1, 1.2, 4.0, 4.4][:days],
            "wind_speed_10m_max": [8.7 + d for d in range(days)],
            "wind_gusts_10m_max": [16.2 + d for d in range(days)],
            "wind_direction_10m_dominant": [189 for _ in range(days)],
        },
    }


def air():
    """An Open-Meteo air-quality response, with the hourly PM2.5 series.

    PM2.5 is shaped so a wood-smoke evening shows up clearly: quiet by day,
    a build-up from 18:00, peaking at 21:00 on the second evening.
    """
    times = [(START + datetime.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
             for i in range(72)]
    pm25 = []
    for i in range(72):
        stamp = START + datetime.timedelta(hours=i)
        hour = stamp.hour
        if stamp.date() == datetime.date(2026, 10, 7) and hour == 21:
            pm25.append(34.0)          # the peak
        elif hour >= 18 or hour < 2:
            pm25.append(26.0)          # smoky evening
        else:
            pm25.append(6.0)           # clean daytime

    return {
        "timezone": "Europe/Athens",
        "current": {
            "time": "2026-10-06T22:00",
            "interval": 3600,
            "european_aqi": 31,
            "pm2_5": 10.3,
            "pm10": 13.0,
            "alder_pollen": 0.0,
            "birch_pollen": 0.0,
            "grass_pollen": 0.3,
            "mugwort_pollen": 1.0,
            "olive_pollen": 0.0,
            "ragweed_pollen": 0.0,
        },
        "hourly": {
            "time": times,
            "pm2_5": pm25,
            "pm10": [round(v * 1.25, 1) for v in pm25],
            "european_aqi": [int(min(100, v * 2)) for v in pm25],
        },
    }


def daily_history(days=46, end=datetime.date(2026, 10, 6)):
    """The daily-only call that feeds heating degree days.

    Means ramp steadily downward, so the month-to-date total is easy to
    recompute by hand in a test.
    """
    start = end - datetime.timedelta(days=days - 1)
    times, means, mins, maxs = [], [], [], []
    for index in range(days):
        day = start + datetime.timedelta(days=index)
        mean = round(20.0 - 0.25 * index, 1)
        times.append(day.isoformat())
        means.append(mean)
        mins.append(round(mean - 5.0, 1))
        maxs.append(round(mean + 5.0, 1))
    return {
        "timezone": "Europe/Athens",
        "daily": {
            "time": times,
            "temperature_2m_mean": means,
            "temperature_2m_min": mins,
            "temperature_2m_max": maxs,
        },
    }


def _info(area_desc, geocode, language, event, headline, description,
          onset, expires, awareness_type, awareness_level, severity):
    return {
        "area": [{"areaDesc": area_desc, "geocode": [
            {"value": geocode, "valueName": "EMMA_ID"}]}],
        "category": ["Met"],
        "certainty": "Likely",
        "description": description,
        "effective": onset,
        "event": event,
        "expires": expires,
        "headline": headline,
        "language": language,
        "onset": onset,
        "parameter": [
            {"value": awareness_type, "valueName": "awareness_type"},
            {"value": awareness_level, "valueName": "awareness_level"},
        ],
        "senderName": "Hnms Forecaster",
        "severity": severity,
        "urgency": "Expected",
        "web": "https://www.emy.gr",
    }


def alerts():
    """A Meteoalarm Greece feed: one for us, one elsewhere, one expired, one cancelled."""
    onset = "2026-10-06T18:00:00+00:00"
    expires = "2026-10-07T06:00:00+00:00"
    return {"warnings": [
        # Relevant to Florina, bilingual: the Greek block must win.
        {"alert": {
            "identifier": "GR.WEST.MAC.1",
            "incidents": "Update",
            "msgType": "Update",
            "info": [
                _info("West Macedonia", "GR009", "en-GB", "Yellow Warning",
                      "Yellow warning for West Macedonia",
                      "Locally gale force winds 8 beaufort.",
                      onset, expires, "1; Wind", "2; Yellow; Moderate", "Moderate"),
                _info("&Delta;&upsilon;&tau;&iota;&kappa;ή &Mu;&alpha;&kappa;&epsilon;&delta;&omicron;&nu;ία",
                      "GR009", "el-GR", "Κίτρινη Προειδοποίηση",
                      "Κίτρινη προειδοποίηση για Δυτική Μακεδονία",
                      "Τοπικά θυελλώδεις άνεμοι έντασης 8 μποφόρ.",
                      onset, expires, "1; Wind", "2; Yellow; Moderate", "Moderate"),
            ],
        }},
        # A different region entirely.
        {"alert": {
            "identifier": "GR.CRETE.2",
            "incidents": "Update",
            "msgType": "Update",
            "info": [_info("Kriti", "GR016", "en-GB", "Orange Warning",
                           "Orange warning for Kriti", "Heavy rain.", onset, expires,
                           "10; Rain", "3; Orange; Severe", "Severe")],
        }},
        # Already over.
        {"alert": {
            "identifier": "GR.WEST.MAC.OLD",
            "incidents": "Update",
            "msgType": "Update",
            "info": [_info("West Macedonia", "GR009", "el-GR", "Κίτρινη Προειδοποίηση",
                           "Παλιά προειδοποίηση", "Έχει λήξει.",
                           "2026-09-01T00:00:00+00:00", "2026-09-01T06:00:00+00:00",
                           "1; Wind", "2; Yellow; Moderate", "Moderate")],
        }},
        # Withdrawn.
        {"alert": {
            "identifier": "GR.WEST.MAC.CANCEL",
            "incidents": "Cancel",
            "msgType": "Cancel",
            "info": [_info("West Macedonia", "GR009", "el-GR", "Κίτρινη Προειδοποίηση",
                           "Ακυρωμένη", "Ακυρώθηκε.", onset, expires,
                           "1; Wind", "2; Yellow; Moderate", "Moderate")],
        }},
    ]}


def dumps(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
