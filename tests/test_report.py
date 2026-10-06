# -*- coding: utf-8 -*-
"""Tests for the report layer: pure shaping of synthetic upstream payloads."""

import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import report  # noqa: E402
import sources  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 21, 59)


def snapshot(**overrides):
    data = {
        "forecast": fixtures.forecast(),
        "air": fixtures.air(),
        "history": fixtures.daily_history(),
        "alerts": fixtures.alerts(),
        "ages": {"forecast": 4.0, "air": 60.0, "history": 120.0, "alerts": 30.0},
        "errors": {},
        "stale": False,
    }
    data.update(overrides)
    return data


def build(**overrides):
    return report.build_report(snapshot(**overrides), sources.Config(), now=NOW)


class HourlyTests(unittest.TestCase):
    """The headline bug in the old version: the hourly strip started at 02:00."""

    def test_starts_at_the_current_hour(self):
        data = build()
        self.assertEqual(data["hourly"][0]["time"], "21:00")
        self.assertEqual(data["hourly"][0]["iso"], "2026-10-06T21:00")

    def test_has_the_configured_number_of_hours(self):
        self.assertEqual(len(build()["hourly"]), sources.Config().forecast_hours)

    def test_hours_are_consecutive_and_cross_midnight(self):
        hours = build()["hourly"]
        for index in range(1, len(hours)):
            before = datetime.datetime.fromisoformat(hours[index - 1]["iso"])
            after = datetime.datetime.fromisoformat(hours[index]["iso"])
            self.assertEqual((after - before).total_seconds(), 3600)
        # 21:00 -> 22:00 -> 23:00 -> 00:00 next day
        self.assertEqual(hours[3]["time"], "00:00")
        self.assertEqual(hours[3]["day"], "2026-10-07")

    def test_night_hours_use_the_night_emoji(self):
        hours = build()["hourly"]
        self.assertFalse(hours[0]["is_day"])
        self.assertEqual(hours[0]["emoji"], "\U0001f319")  # moon, not sun

    def test_daytime_hours_use_the_day_emoji(self):
        hours = build()["hourly"]
        noon = [h for h in hours if h["time"] == "12:00"][0]
        self.assertTrue(noon["is_day"])
        self.assertNotEqual(noon["emoji"], "\U0001f319")

    def test_wind_fields(self):
        hour = build()["hourly"][0]
        self.assertEqual(hour["wind_dir_text"], "ΝΔ")
        self.assertTrue(hour["wind_arrow"])

    def test_row_lengths_are_consistent(self):
        for hour in build()["hourly"]:
            for key in ("time", "temp", "emoji", "text", "precip_prob", "wind", "uv"):
                self.assertIn(key, hour)
                self.assertIsNotNone(hour[key], "missing %s" % key)


class CurrentTests(unittest.TestCase):

    def test_core_fields(self):
        current = build()["current"]
        self.assertEqual(current["temp"], 11.1)
        self.assertEqual(current["humidity"], 49)
        self.assertEqual(current["text"], "Καθαρός")
        # 7.9 km/h is 4.3 knots: Beaufort 2.
        self.assertEqual(current["beaufort"], 2)
        self.assertEqual(current["beaufort_text"], "Πολύ ασθενής")
        self.assertEqual(current["pressure"], 1022.5)

    def test_visibility_is_lifted_from_the_current_hour(self):
        self.assertEqual(build()["current"]["visibility"], 41220.0)
        self.assertEqual(build()["current"]["visibility_text"], "Καθαρή ατμόσφαιρα")

    def test_behaviour_at_the_end_of_the_forecast(self):
        # A "now" past the end of the hourly array must not raise.
        late = datetime.datetime(2026, 10, 13, 3, 0)
        data = report.build_report(snapshot(), sources.Config(), now=late)
        self.assertTrue(data["hourly"])
        self.assertEqual(len(data["hourly"]), 1)  # only the final slot remains


class DailyTests(unittest.TestCase):

    def test_first_day_is_today(self):
        today = build()["today"]
        self.assertEqual(today["label"], "Σήμερα")
        self.assertEqual(today["weekday"], "Τρίτη")
        self.assertEqual(today["month"], "Οκτωβρίου")
        self.assertEqual(today["max"], 22.4)
        self.assertEqual(today["min"], 6.4)

    def test_sun_times_are_hh_mm(self):
        # The old template rendered the whole "2026-10-06T07:35" string.
        today = build()["today"]
        self.assertEqual(today["sunrise"], "07:35")
        self.assertEqual(today["sunset"], "19:08")
        self.assertNotIn("T", today["sunrise"])

    def test_daylight_duration_is_readable(self):
        self.assertEqual(build()["today"]["daylight"], "11ω 32λ")

    def test_uv_level_is_labelled(self):
        today = build()["today"]
        self.assertEqual(today["uv_level"], "Μέτριος")
        self.assertTrue(today["uv_color"].startswith("#"))

    def test_seven_days_and_second_label(self):
        days = build()["daily"]
        self.assertEqual(len(days), 7)
        self.assertEqual(days[1]["label"], "Αύριο")
        self.assertEqual(days[2]["weekday"], "Πέμπτη")


class AlertTests(unittest.TestCase):

    def test_only_our_region_survives(self):
        alerts = build()["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["identifier"], "GR.WEST.MAC.1")

    def test_greek_block_is_preferred_and_entities_decoded(self):
        alert = build()["alerts"][0]
        self.assertEqual(alert["area"], "Δυτική Μακεδονία")
        self.assertIn("θυελλώδεις", alert["description"])

    def test_hazard_and_level(self):
        alert = build()["alerts"][0]
        self.assertEqual(alert["hazard"], "Άνεμος")
        self.assertEqual(alert["level"], "yellow")
        self.assertEqual(alert["level_label"], "Κίτρινη")

    def test_expired_and_cancelled_are_dropped(self):
        identifiers = [a["identifier"] for a in build()["alerts"]]
        self.assertNotIn("GR.WEST.MAC.OLD", identifiers)
        self.assertNotIn("GR.WEST.MAC.CANCEL", identifiers)

    def test_time_window_is_localised(self):
        alert = build()["alerts"][0]
        # 18:00 UTC is 21:00 in Athens (EEST, +03:00).
        self.assertEqual(alert["onset"], "2026-10-06T21:00")
        self.assertEqual(alert["expires"], "2026-10-07T09:00")
        self.assertTrue(alert["onset_text"].startswith("Σήμερα"))
        self.assertTrue(alert["expires_text"].startswith("Αύριο"))

    def test_area_matching_is_accent_and_case_insensitive(self):
        config = sources.Config(alert_areas=["ΔΥΤΙΚΗ ΜΑΚΕΔΟΝΙΑ"])
        data = report.build_report(snapshot(), config, now=NOW)
        self.assertEqual(len(data["alerts"]), 1)

    def test_matching_by_emma_id(self):
        config = sources.Config(alert_areas=[], alert_emma_ids=["GR009"])
        data = report.build_report(snapshot(), config, now=NOW)
        self.assertEqual(len(data["alerts"]), 1)

    def test_no_filter_keeps_everything_current(self):
        config = sources.Config(alert_areas=[], alert_emma_ids=[])
        data = report.build_report(snapshot(), config, now=NOW)
        # Kriti + West Macedonia; the expired and cancelled ones are still gone.
        self.assertEqual(len(data["alerts"]), 2)

    def test_no_alerts_is_not_an_error(self):
        self.assertEqual(build(alerts=None)["alerts"], [])
        self.assertEqual(build(alerts={"warnings": []})["alerts"], [])

    def test_red_warning_sorts_first(self):
        raw = {"warnings": [dict(w) for w in fixtures.alerts()["warnings"]]}
        # Promote the Kriti warning to Red and add it to our region.
        block = raw["warnings"][1]["alert"]["info"][0]
        block["area"][0]["areaDesc"] = "West Macedonia"
        block["parameter"][1]["value"] = "4; Red; Extreme"
        block["severity"] = "Extreme"
        alerts = report.parse_alerts(
            {"warnings": [raw["warnings"][0], raw["warnings"][1]]},
            sources.Config(), datetime.datetime(2026, 10, 6, 21, 59,
                                                tzinfo=datetime.timezone.utc))
        self.assertEqual(alerts[0]["level"], "red")


class LocalConditionTests(unittest.TestCase):
    """The hyper-local block: frost, heating degree days and wood smoke."""

    # -- frost -------------------------------------------------------------

    def test_frost_is_absent_when_no_night_gets_near_freezing(self):
        # The default fixture is mild, so the card should simply not exist.
        self.assertIsNone((build()["local"] or {}).get("frost"))

    def _with_minima(self, minima):
        forecast = fixtures.forecast()
        forecast["daily"]["temperature_2m_min"] = list(minima)
        return report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)

    def test_frost_reports_the_coldest_night(self):
        data = self._with_minima([3.0, -1.4, 0.5, -2.6, 5.0, 4.0, 3.0])
        frost = data["local"]["frost"]
        self.assertEqual(frost["lowest"], -2.6)
        self.assertEqual(frost["level"], "hard")      # -2.6 crosses the -2 band
        self.assertEqual(frost["count"], 2)           # -1.4 and -2.6
        self.assertEqual(frost["first"]["iso"], "2026-10-07")

    def test_frost_levels_follow_the_bands(self):
        cases = [(-6.0, "severe"), (-3.0, "hard"), (-0.5, "frost"), (1.5, "risk")]
        for value, expected in cases:
            data = self._with_minima([value, 5, 5, 5, 5, 5, 5])
            self.assertEqual(data["local"]["frost"]["level"], expected,
                             "%s C should be %s" % (value, expected))

    def test_mild_nights_are_not_counted_as_frost(self):
        data = self._with_minima([1.0, 1.8, 5.0, 5.0, 5.0, 5.0, 5.0])
        frost = data["local"]["frost"]
        self.assertEqual(frost["count"], 0)           # nothing below zero
        self.assertEqual(frost["level"], "risk")      # but still worth a word

    # -- heating degree days -----------------------------------------------

    def test_heating_totals_today_and_the_month_so_far(self):
        # Derive the expectation from the fixture itself, so this tests the
        # arithmetic rather than the fixture's rounding.
        history = fixtures.daily_history()["daily"]
        october = [mean for stamp, mean in
                   zip(history["time"], history["temperature_2m_mean"])
                   if stamp.startswith("2026-10")]
        heating = build()["local"]["heating"]
        self.assertAlmostEqual(
            heating["month"], sum(max(0.0, 18.0 - m) for m in october), places=3)
        self.assertEqual(heating["month_days"], len(october))
        self.assertAlmostEqual(
            heating["today"], max(0.0, 18.0 - history["temperature_2m_mean"][-1]),
            places=3)
        self.assertEqual(heating["month_name"], "Οκτωβρίου")
        self.assertEqual(heating["base"], 18.0)

    def test_heating_hides_itself_when_nothing_needs_heating(self):
        history = fixtures.daily_history()
        history["daily"]["temperature_2m_mean"] = [25.0] * 46
        data = report.build_report(snapshot(history=history),
                                   sources.Config(), now=NOW)
        self.assertIsNone((data["local"] or {}).get("heating"))

    def test_heating_needs_the_history_source(self):
        data = build(history=None)
        self.assertNotIn("heating", data["local"] or {})

    # -- wood smoke --------------------------------------------------------

    def test_smog_finds_the_evening_peak(self):
        smog = build()["local"]["smog"]
        self.assertEqual(smog["peak"], 34.0)
        self.assertEqual(smog["peak_time"], "21:00")
        self.assertEqual(smog["level"], "warn")       # 34 ug/m3 is over 25
        self.assertEqual(smog["label"], "Αιθαλομίχλη")

    def test_smog_reports_the_calm_wind_that_lets_it_pool(self):
        smog = build()["local"]["smog"]
        self.assertAlmostEqual(smog["wind"], 7.9, places=2)
        self.assertTrue(smog["calm"])                 # 7.9 km/h is under 12

    def test_smog_suggests_when_the_air_is_cleanest(self):
        smog = build()["local"]["smog"]
        self.assertLess(smog["cleanest"], smog["peak"])
        self.assertRegex(smog["cleanest_time"], r"^\d{2}:\d{2}$")

    def test_clean_air_means_no_smog_card(self):
        air = fixtures.air()
        air["hourly"]["pm2_5"] = [4.0] * 72
        data = report.build_report(snapshot(air=air), sources.Config(), now=NOW)
        self.assertIsNone((data["local"] or {}).get("smog"))

    def test_smog_survives_a_missing_wind_series(self):
        forecast = fixtures.forecast()
        forecast["hourly"].pop("wind_speed_10m")
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        smog = (data["local"] or {}).get("smog")
        self.assertIsNotNone(smog)
        self.assertIsNone(smog["wind"])
        self.assertFalse(smog["calm"])

    # -- assembly ----------------------------------------------------------

    def test_the_block_is_absent_entirely_when_nothing_applies(self):
        forecast = fixtures.forecast()
        forecast["daily"]["temperature_2m_min"] = [12.0] * 7
        air = fixtures.air()
        air["hourly"]["pm2_5"] = [3.0] * 72
        history = fixtures.daily_history()
        history["daily"]["temperature_2m_mean"] = [26.0] * 46
        data = report.build_report(
            snapshot(forecast=forecast, air=air, history=history),
            sources.Config(), now=NOW)
        self.assertIsNone(data["local"])

    def test_local_is_carried_in_the_payload(self):
        self.assertIn("local", build())

    def test_no_float_noise_reaches_the_client(self):
        """A raw 3.9000000000000004 in the JSON is sloppy and needless."""
        import json as _json
        blob = _json.dumps(build()["local"], ensure_ascii=False)
        for token in blob.replace('"', " ").split():
            if "." not in token or not token.split(".")[-1][:1].isdigit():
                continue
            decimals = len(token.split(".")[-1].rstrip("},"))
            self.assertLessEqual(decimals, 1, "too many decimals: " + token)




    def test_values(self):
        air = build()["air"]
        self.assertEqual(air["aqi"], 31)
        self.assertEqual(air["label"], "Αποδεκτή")
        self.assertEqual(air["pm2_5"], 10.3)

    def test_zero_pollens_are_hidden_and_order_is_descending(self):
        pollens = build()["air"]["pollens"]
        names = [p["name"] for p in pollens]
        self.assertNotIn("Γύρη σκλήθρου", names)   # 0.0
        self.assertEqual(pollens[0]["name"], "Γύρη αψιθιάς")  # 1.0 > 0.3

    def test_missing_air_data_is_fine(self):
        self.assertIsNone(build(air=None)["air"])


class DegradedTests(unittest.TestCase):

    def test_status_reports_errors_and_age(self):
        status = build(errors={"alerts": "boom"})["status"]
        self.assertEqual(status["degraded"], ["alerts"])
        self.assertEqual(status["age"], 4.0)
        self.assertFalse(status["stale"])

    def test_stale_flag_is_carried_through(self):
        self.assertTrue(build(stale=True)["status"]["stale"])


class SummaryTests(unittest.TestCase):

    def test_mentions_the_condition_and_the_range(self):
        summary = build()["summary"]
        self.assertIn("Καθαρός", summary)
        self.assertIn("11", summary)
        self.assertIn("Σήμερα", summary)

    def test_mentions_rain_when_expected(self):
        forecast = fixtures.forecast()
        forecast["daily"]["precipitation_sum"][0] = 7.5
        data = report.build_report(snapshot(forecast=forecast), sources.Config(), now=NOW)
        self.assertIn("βροχή", data["summary"])

    def test_mentions_alerts(self):
        self.assertIn("προειδοποίηση", build()["summary"])


class ErrorTests(unittest.TestCase):

    def test_missing_forecast_raises(self):
        with self.assertRaises(report.ReportError):
            build(forecast=None)

    def test_forecast_without_current_raises(self):
        forecast = fixtures.forecast()
        forecast.pop("current")
        with self.assertRaises(report.ReportError):
            build(forecast=forecast)

    def test_forecast_without_hourly_raises(self):
        forecast = fixtures.forecast()
        forecast["hourly"] = {"time": []}
        with self.assertRaises(report.ReportError):
            build(forecast=forecast)


class NormaliseTests(unittest.TestCase):

    def test_accents_entities_and_case(self):
        self.assertEqual(report._normalise("&amp;West  Macedonia"), "&west macedonia")
        self.assertEqual(report._normalise("Δυτική Μακεδονία"), "δυτικη μακεδονια")
        self.assertEqual(report._normalise("  A   B  "), "a b")
        self.assertEqual(report._normalise(None), "")
        # A final sigma casefolds to the same letter as an ordinary sigma, so
        # "Άνεμος" and "Άνεμοσ" compare equal.
        self.assertEqual(report._normalise("Άνεμος"), report._normalise("Άνεμοσ"))


class TimezoneTests(unittest.TestCase):
    """Windows Python ships without tzdata, so this path matters in practice."""

    def test_offset_falls_back_to_the_api_value(self):
        original = report._zone
        report._zone = lambda name: None
        try:
            tz, source = report.resolve_timezone("Europe/Athens", fixtures.forecast())
            self.assertEqual(source, "utc_offset")
            self.assertEqual(tz.utcoffset(None).total_seconds(), 10800)
        finally:
            report._zone = original

    def test_offset_falls_back_to_utc_without_the_api_value(self):
        original = report._zone
        report._zone = lambda name: None
        try:
            tz, source = report.resolve_timezone("Europe/Athens", {})
            self.assertEqual(source, "utc")
            self.assertEqual(tz.utcoffset(None).total_seconds(), 0)
        finally:
            report._zone = original

    def test_report_works_without_a_timezone_database(self):
        original = report._zone
        report._zone = lambda name: None
        try:
            data = report.build_report(snapshot(), sources.Config(), now=NOW)
            self.assertEqual(data["status"]["timezone_source"], "utc_offset")
            # 18:00 UTC -> 21:00 local, exactly as with the IANA database.
            self.assertEqual(data["alerts"][0]["onset"], "2026-10-06T21:00")
            self.assertTrue(data["current"]["date_text"])
        finally:
            report._zone = original

    def test_source_is_reported(self):
        self.assertIn(build()["status"]["timezone_source"], ("zoneinfo", "utc_offset", "utc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
