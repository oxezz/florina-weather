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
import greek  # noqa: E402
import report  # noqa: E402
import sources  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 21, 59)


def snapshot(**overrides):
    data = {
        "forecast": fixtures.forecast(),
        "air": fixtures.air(),
        "history": fixtures.daily_history(),
        "terrain": fixtures.terrain(),
        "normals": fixtures.normals(),
        "snow": fixtures.snow(),
        "station": fixtures.station(),
        "alerts": fixtures.alerts(),
        "ages": {"forecast": 4.0, "air": 60.0, "history": 120.0,
                 "terrain": 90.0, "alerts": 30.0},
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

    def test_night_hours_use_the_real_moon(self):
        """Not a stock crescent: the strip, the hero and the moon row all read
        from the same phase, so they cannot contradict each other."""
        data = build()
        hours = data["hourly"]
        self.assertFalse(hours[0]["is_day"])
        self.assertEqual(hours[0]["emoji"], data["sky"]["emoji"])
        self.assertEqual(hours[0]["emoji"], "\U0001f318")   # waning crescent

    def test_the_first_card_agrees_with_the_hero(self):
        """Both are labelled «τώρα». `current` is interpolated every 15 minutes
        while `hourly[0]` is the top of the hour, and the two genuinely differ,
        so the card takes the hero's reading rather than its own."""
        data = build()
        first = data["hourly"][0]
        for field in ("temp", "code", "text", "emoji", "apparent", "wind"):
            self.assertEqual(first[field], data["current"][field],
                             "«τώρα» disagrees with the hero on %s" % field)

    def test_daytime_hours_use_the_day_emoji(self):
        hours = build()["hourly"]
        noon = [h for h in hours if h["time"] == "12:00"][0]
        self.assertTrue(noon["is_day"])
        self.assertNotEqual(noon["emoji"], "\U0001f318")

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
        """Set the daily air minima, leaving the ground series at its default.

        The fixture's ground series bottoms out at 7.0 °C each day, so these
        cases exercise the air sensor on its own.
        """
        forecast = fixtures.forecast()
        forecast["daily"]["temperature_2m_min"] = list(minima)
        return report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)

    def _with_ground(self, air_minima, ground_minima):
        """Set both sensors. The ground series is written flat per day, so its
        daily minimum is exactly the value asked for."""
        forecast = fixtures.forecast()
        forecast["daily"]["temperature_2m_min"] = list(air_minima)
        series = []
        for stamp in forecast["hourly"]["time"]:
            offset = (datetime.date.fromisoformat(stamp[:10])
                      - datetime.date(2026, 10, 6)).days
            series.append(ground_minima[offset]
                          if 0 <= offset < len(ground_minima) else 10.0)
        forecast["hourly"]["soil_temperature_0cm"] = series
        return report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)

    def test_frost_reports_the_coldest_night(self):
        data = self._with_minima([3.0, -1.4, 0.5, -2.6, 5.0, 4.0, 3.0])
        frost = data["local"]["frost"]
        self.assertEqual(frost["min"], -2.6)
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

    # -- ground frost: the sensor the 2 m air misses -----------------------

    def test_ground_frost_is_caught_while_the_air_stays_above_zero(self):
        """A clear calm night radiates heat off the surface, which can freeze
        while the air at 2 m is still positive — the radiation frost that
        catches low crops such as peppers."""
        data = self._with_ground([2.5, 3.0, 4.0, 5.0, 6.0, 5.0, 4.0],
                                 [-1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0])
        frost = (data["local"] or {}).get("frost")
        self.assertIsNotNone(frost, "air +2.5 C should not hide a frozen surface")
        self.assertEqual(frost["min"], -1.0)
        self.assertEqual(frost["level"], "frost")
        self.assertEqual(frost["count"], 1)
        self.assertEqual(frost["driver"], "ground")
        self.assertEqual(frost["air_min"], 2.5)
        self.assertEqual(frost["ground_min"], -1.0)

    def test_the_air_still_wins_when_it_is_the_colder_sensor(self):
        data = self._with_ground([-5.0, 5, 5, 5, 5, 5, 5],
                                 [-2.0, 10, 10, 10, 10, 10, 10])
        frost = data["local"]["frost"]
        self.assertEqual(frost["min"], -5.0)
        self.assertEqual(frost["driver"], "air")
        self.assertEqual(frost["level"], "severe")

    def test_a_frozen_surface_raises_the_severity(self):
        # +1.5 C air alone would read as a mild risk; a -2.5 C surface does not.
        alone = self._with_minima([1.5, 5, 5, 5, 5, 5, 5])["local"]["frost"]
        with_ground = self._with_ground([1.5, 5, 5, 5, 5, 5, 5],
                                        [-2.5, 10, 10, 10, 10, 10, 10])["local"]["frost"]
        self.assertEqual(alone["level"], "risk")
        self.assertEqual(with_ground["level"], "hard")

    def test_a_missing_ground_series_falls_back_to_air_alone(self):
        forecast = fixtures.forecast()
        forecast["daily"]["temperature_2m_min"] = [3.0, -1.4, 5, 5, 5, 5, 5]
        forecast["hourly"].pop("soil_temperature_0cm")
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        frost = data["local"]["frost"]
        self.assertIsNone(frost["ground_min"])
        self.assertEqual(frost["min"], -1.4)
        self.assertEqual(frost["driver"], "air")

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
        self.assertTrue(smog["calm"])
        self.assertTrue(smog["pooling"])
        self.assertEqual(smog["label"], "Αιθαλομίχλη")

    def test_a_breeze_means_it_is_not_wood_smoke(self):
        """The same PM2.5 with a real wind is particulate passing through, not
        smoke pooling in the basin, so the smog framing would be wrong."""
        forecast = fixtures.forecast()
        forecast["hourly"]["wind_speed_10m"] = [28.0] * len(forecast["hourly"]["time"])
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        smog = (data["local"] or {}).get("smog")
        self.assertIsNotNone(smog)
        self.assertFalse(smog["pooling"])
        self.assertFalse(smog["calm"])
        self.assertGreater(smog["wind"], 12.0)
        # The health-relevant severity is unchanged; only the framing is.
        self.assertEqual(smog["level"], "warn")
        self.assertEqual(smog["peak"], 34.0)
        self.assertEqual(smog["label"], "Αυξημένα σωματίδια")

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
            snapshot(forecast=forecast, air=air, history=history,
                     # A normal lapse, so the inversion block stays out too.
                     terrain=fixtures.terrain(valley_temp=10.0, slope_temp=7.3)),
            sources.Config(), now=NOW)
        self.assertIsNone(data["local"])

    def test_local_is_carried_in_the_payload(self):
        self.assertIn("local", build())

    def test_no_float_noise_reaches_the_client(self):
        """A raw 3.9000000000000004 in the JSON is sloppy and needless. Two
        decimals are allowed: the lapse rate needs them to mean anything."""
        import json as _json
        blob = _json.dumps(build()["local"], ensure_ascii=False)
        for token in blob.replace('"', " ").split():
            if "." not in token or not token.split(".")[-1][:1].isdigit():
                continue
            decimals = len(token.split(".")[-1].rstrip("},"))
            self.assertLessEqual(decimals, 2, "too many decimals: " + token)




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


class InversionTests(unittest.TestCase):
    """The basin inversion, read from the valley against a slope."""

    def _with_terrain(self, **kwargs):
        return report.build_report(snapshot(terrain=fixtures.terrain(**kwargs)),
                                   sources.Config(), now=NOW)

    def test_a_warm_slope_over_a_cold_valley_is_an_inversion(self):
        # The fixture: 10.3 C at 1073 m against 8.4 C at 662 m. A standard
        # atmosphere would put the slope 2.67 C colder, so this is a strong
        # one - and the anomaly carries the measured night-bias correction.
        inversion = self._with_terrain()["local"]["inversion"]
        self.assertIsNotNone(inversion)
        self.assertEqual(inversion["level"], "strong")
        self.assertAlmostEqual(inversion["delta"], 1.9, places=1)
        self.assertAlmostEqual(inversion["anomaly"], 4.6 + report.INVERSION_BIAS,
                               places=1)
        self.assertGreater(inversion["lapse"], 0)      # rising with height
        self.assertEqual(inversion["valley_elev"], 662)
        self.assertEqual(inversion["slope_elev"], 1073)
        self.assertEqual(inversion["models"], 4)
        self.assertTrue(inversion["confident"])   # the fixture models agree

    def test_the_night_bias_is_applied_and_reported(self):
        """The index is a difference of two modelled temperatures, so a bias
        that is not common to both lands in it. Corrected, and the figure is
        exposed rather than baked in silently."""
        inversion = self._with_terrain()["local"]["inversion"]
        self.assertEqual(inversion["bias"], report.INVERSION_BIAS)
        self.assertGreater(report.INVERSION_BIAS, 0)      # understated, not over
        self.assertLess(report.INVERSION_BIAS_RANGE, report.INVERSION_BIAS)
        # The raw pair is still shown, so the correction can be undone by eye.
        self.assertAlmostEqual(inversion["valley_raw"] - inversion["bias"],
                               inversion["valley_temp"], places=1)

    def test_disagreement_bigger_than_the_signal_reads_as_uncertain(self):
        """If the models straddle the anomaly by more than the anomaly itself,
        some of them are saying there is no inversion at all."""
        data = self._with_terrain(valley_temp=10.0, slope_temp=9.0,
                                  slope_offsets={"best_match": -3.0,
                                                 "gfs_seamless": 3.0})
        inversion = data["local"]["inversion"]
        self.assertFalse(inversion["confident"])
        self.assertEqual(inversion["level"], "uncertain")
        self.assertEqual(inversion["label"], "Πιθανή αναστροφή")
        self.assertGreater(inversion["spread"], abs(inversion["anomaly"]))

    def test_normal_lapse_means_no_card(self):
        # Slope 2.7 C colder than the valley: exactly the standard atmosphere.
        data = self._with_terrain(valley_temp=10.0, slope_temp=7.3)
        self.assertIsNone((data["local"] or {}).get("inversion"))

    def test_a_weaker_than_standard_lapse_crosses_the_band(self):
        # 2.0 C drop where standard predicts 2.67 is not enough even after the
        # correction; 1.0 is comfortably over.
        data = self._with_terrain(valley_temp=10.0, slope_temp=8.0)
        self.assertIsNone((data["local"] or {}).get("inversion"))
        data = self._with_terrain(valley_temp=10.0, slope_temp=9.0)
        self.assertEqual(data["local"]["inversion"]["level"], "inversion")

    def test_a_case_the_correction_moves_says_possible_not_certain(self):
        """The correction is 0.73 with an uncertainty near 0.4, so where it
        pushes a reading across a band edge the card must not claim a definite
        inversion. Without this the threshold silently drops from 1.5 to 0.8."""
        data = self._with_terrain(valley_temp=10.0, slope_temp=8.5)
        inversion = (data["local"] or {}).get("inversion")
        self.assertIsNotNone(inversion)
        self.assertEqual(inversion["level"], "uncertain")
        self.assertFalse(inversion["confident"])

    def test_a_higher_slope_than_the_valley_never_triggers(self):
        data = self._with_terrain(valley_z=1200.0, slope_z=800.0)
        self.assertIsNone((data["local"] or {}).get("inversion"))

    def test_missing_terrain_is_not_an_error(self):
        for broken in (None, [], [{"elevation": 662.0}]):
            data = report.build_report(snapshot(terrain=broken),
                                       sources.Config(), now=NOW)
            self.assertIsNone((data["local"] or {}).get("inversion"))

    def test_model_disagreement_about_the_profile_is_reported(self):
        """Only disagreement about the *vertical difference* moves the
        anomaly; a bias applied equally to both places cancels out."""
        data = self._with_terrain(slope_offsets={"icon_eu": -0.5,
                                                 "gfs_seamless": 0.5})
        self.assertAlmostEqual(data["local"]["inversion"]["spread"], 1.0, places=1)

    def test_a_uniform_model_bias_cancels_out_of_the_difference(self):
        both = {"icon_eu": -0.5, "gfs_seamless": 0.5}
        data = self._with_terrain(offsets=both, slope_offsets=both)
        self.assertAlmostEqual(data["local"]["inversion"]["spread"], 0.0, places=1)


class AgreementTests(unittest.TestCase):
    """How far apart the models are, per day."""

    def _days_with(self, offsets):
        data = report.build_report(
            snapshot(terrain=fixtures.terrain(offsets=offsets)),
            sources.Config(), now=NOW)
        return data["daily"]

    def test_agreeing_models_read_tight(self):
        days = self._days_with({"best_match": 0.0, "icon_eu": 0.0,
                                "ecmwf_ifs025": 0.0, "gfs_seamless": 0.0})
        for day in days:
            self.assertEqual(day["agreement"]["spread"], 0.0)
            self.assertEqual(day["agreement"]["level"], "tight")
            self.assertEqual(day["agreement"]["models"], 4)

    def test_a_five_degree_split_reads_as_disagreement(self):
        days = self._days_with({"icon_eu": 0.0, "ecmwf_ifs025": 1.5,
                                "gfs_seamless": 5.0})
        first = days[0]["agreement"]
        self.assertEqual(first["spread"], 5.0)
        self.assertEqual(first["level"], "wide")
        self.assertEqual(first["range"], [20.0, 25.0])

    def test_a_moderate_split_sits_in_the_middle(self):
        days = self._days_with({"icon_eu": 0.0, "ecmwf_ifs025": 0.0,
                                "gfs_seamless": 2.5})
        self.assertEqual(days[0]["agreement"]["level"], "fair")

    def test_one_model_alone_is_not_a_disagreement(self):
        data = report.build_report(
            snapshot(terrain=fixtures.terrain(models=("icon_eu",))),
            sources.Config(), now=NOW)
        for day in data["daily"]:
            self.assertIsNone(day["agreement"])

    def test_missing_terrain_leaves_the_days_intact(self):
        data = report.build_report(snapshot(terrain=None),
                                   sources.Config(), now=NOW)
        self.assertEqual(len(data["daily"]), 7)
        for day in data["daily"]:
            self.assertIsNone(day["agreement"])


class GreetingTests(unittest.TestCase):
    """The one-line hello at the top of the page."""

    def _greeting(self, **current):
        data = report.build_report(snapshot(), sources.Config(), now=NOW)
        merged = dict(data["current"])
        merged.update(current)
        return report.build_greeting(NOW, merged)

    def test_the_word_follows_the_hour(self):
        import datetime as _dt
        morning = report.build_greeting(
            _dt.datetime(2026, 10, 6, 8, 0), {"code": 0, "is_day": 1})
        self.assertEqual(morning["word"], "Καλημέρα")
        evening = report.build_greeting(
            _dt.datetime(2026, 10, 6, 20, 0), {"code": 0, "is_day": 0})
        self.assertEqual(evening["word"], "Καλησπέρα")

    def test_it_never_greets_with_a_farewell(self):
        """Καληνύχτα means goodbye in Greek, so an app must not open with it."""
        for hour in range(24):
            word = greek.greeting_word(hour)
            self.assertIn(word, ("Καλημέρα", "Καλησπέρα"))
            self.assertNotEqual(word, "Καληνύχτα")

    def test_weather_picks_the_line(self):
        cases = [
            ({"code": 73}, "snow"),
            ({"code": 95}, "storm"),
            ({"code": 61}, "rain"),
            ({"code": 45}, "fog"),
            ({"code": 3, "apparent": -2.0}, "frost"),
            ({"code": 3, "apparent": 4.0}, "cold"),
            ({"code": 3, "temp": 36.0, "apparent": 36.0}, "heat"),
            ({"code": 0, "is_day": 0, "apparent": 15.0}, "clear-night"),
            ({"code": 0, "is_day": 1, "apparent": 15.0}, "clear-day"),
            ({"code": 2, "apparent": 15.0}, "cloud"),
        ]
        for current, expected in cases:
            self.assertEqual(self._greeting(**current)["key"], expected,
                             "%s should read as %s" % (current, expected))

    def test_severe_weather_beats_a_mild_temperature(self):
        # 25 C but a thunderstorm: the storm is what matters.
        self.assertEqual(
            self._greeting(code=95, temp=25.0, apparent=25.0)["key"], "storm")

    def test_every_key_has_a_line(self):
        for key in greek.GREETING_LINES:
            self.assertTrue(greek.GREETING_LINES[key].strip())


class OutfitTests(unittest.TestCase):
    """Τι να φορέσω, from the feels-like temperature across the day ahead."""

    @staticmethod
    def hour(apparent, wet=0, gusts=10, uv=0.5, temp=None, wind=None):
        return {"apparent": apparent,
                "temp": apparent if temp is None else temp,
                "precip_prob": wet, "gusts": gusts,
                "wind": (gusts / 2.0) if wind is None else wind, "uv": uv}

    def card(self, hours):
        return report.build_outfit(hours, NOW)

    def keys(self, outfit):
        return [i["key"] for i in outfit["items"]]

    # -- the headline ------------------------------------------------------

    def test_bands_follow_apparent_temperature(self):
        cases = [(-5.0, "severe"), (3.0, "cold"), (9.0, "cool"),
                 (15.0, "mild"), (21.0, "warm"), (28.0, "hot"),
                 (35.0, "scorching")]
        for value, expected in cases:
            self.assertEqual(greek.outfit_layer(value)[0], expected,
                             "%s C should be %s" % (value, expected))

    def test_the_headline_names_actual_garments(self):
        """'Wear a jacket' tells you nothing you did not already know."""
        for value in (-8, 3, 9, 15, 21, 28, 35):
            text = greek.outfit_layer(value)[1]
            self.assertGreater(len(text), 10, "too vague at %s" % value)
            self.assertRegex(text, "(?i)(μπουφάν|ρούχα|ζακέτα|ισοθερμικά|"
                                   "κοντομάνικο|ελαφριά|ζέστη)",
                             "no garment named at %s" % value)

    def test_a_wide_swing_names_both_ends(self):
        """'Layers' on its own is not actionable."""
        cold = self.hour(6.0)
        warm = self.hour(20.0)
        outfit = self.card([cold] * 6 + [warm] * 6)
        self.assertEqual(outfit["key"], "layers")
        self.assertIn("μπουφάν", outfit["text"])
        self.assertIn("κοντομάνικο", outfit["text"])
        # The headline already says it, so no chip repeats it.
        self.assertNotIn("layers", self.keys(outfit))

    def test_a_flat_day_keeps_the_single_answer(self):
        outfit = self.card([self.hour(14.0)] * 12)
        self.assertEqual(outfit["key"], "mild")
        self.assertNotIn("layers", self.keys(outfit))

    def test_the_detail_line_gives_the_range(self):
        outfit = self.card([self.hour(8.0)] * 6 + [self.hour(18.0)] * 6)
        self.assertIn("Αίσθηση", outfit["detail"])
        self.assertIn("8", outfit["detail"])
        self.assertIn("18", outfit["detail"])

    # -- the extras --------------------------------------------------------

    def test_a_wet_afternoon_earns_an_umbrella(self):
        self.assertIn("umbrella", self.keys(self.card([self.hour(12.0, wet=70)])))

    def test_a_maybe_earns_a_maybe(self):
        outfit = self.card([self.hour(12.0, wet=30)])
        self.assertIn("maybe-umbrella", self.keys(outfit))
        self.assertNotIn("umbrella", self.keys(outfit))

    def test_a_gale_turns_the_umbrella_into_a_raincoat(self):
        """The idea worth taking from the reference: a brolly in a gale is
        worse than useless."""
        outfit = self.card([self.hour(4.0, wet=65, gusts=45)])
        self.assertIn("raincoat", self.keys(outfit))
        self.assertNotIn("umbrella", self.keys(outfit))
        self.assertIn("αδιάβροχο", outfit["advice"])

    def test_distant_rain_does_not_earn_one(self):
        # Rain eight hours out is not a decision anyone is making now.
        hours = [self.hour(12.0, wet=5)] * 6 + [self.hour(12.0, wet=95)] * 4
        self.assertNotIn("umbrella", self.keys(self.card(hours)))

    def test_a_strong_sun_earns_sunscreen(self):
        # UV alone earns the sunscreen; the hat needs real heat as well.
        mild = self.card([self.hour(22.0, uv=8)])
        self.assertIn("sunscreen", self.keys(mild))
        self.assertNotIn("hat", self.keys(mild))
        self.assertIn("hat", self.keys(self.card([self.hour(30.0, uv=8)])))

    def test_a_freezing_wet_morning_warns_about_ice(self):
        self.assertIn("ice", self.keys(self.card([self.hour(-1.0, wet=60)])))

    def test_a_gale_is_flagged_on_its_own(self):
        """Wind matters here even in the dry: it is what makes Florina bite."""
        self.assertIn("wind", self.keys(self.card([self.hour(14.0, gusts=55)])))

    def test_several_extras_can_apply_at_once(self):
        """A list, not one alert that has to win: hot, wet, windy and sunny
        all at once is unusual but it is not a reason to drop three of them."""
        outfit = self.card([self.hour(30.0, wet=70, gusts=15, uv=8, wind=12)])
        keys = self.keys(outfit)
        self.assertIn("umbrella", keys)
        self.assertIn("sunscreen", keys)
        self.assertIn("hat", keys)

    def test_only_one_rain_advice_at_a_time(self):
        # Calm rain gets a brolly; a gale swaps it for a raincoat. Never both.
        calm = self.keys(self.card([self.hour(12.0, wet=70, gusts=10)]))
        gale = self.keys(self.card([self.hour(12.0, wet=70, gusts=45)]))
        self.assertIn("umbrella", calm)
        self.assertIn("raincoat", gale)
        self.assertNotIn("umbrella", gale)

    def test_advice_is_omitted_when_the_chips_already_say_it(self):
        self.assertIsNone(self.card([self.hour(15.0)])["advice"])

    def test_freezing_and_scorching_get_a_line(self):
        self.assertIn("Παγωνιά", self.card([self.hour(-8.0)])["advice"])
        self.assertIn("Ζέστη", self.card([self.hour(36.0)])["advice"])

    def test_a_big_swing_gets_a_line(self):
        outfit = self.card([self.hour(-4.0)] * 6 + [self.hour(20.0)] * 6)
        self.assertIn("διαφορά", outfit["advice"])

    def test_no_hours_means_no_card(self):
        self.assertIsNone(report.build_outfit([], NOW))

    def test_a_missing_apparent_temperature_hides_the_card(self):
        self.assertIsNone(report.build_outfit([{"precip_prob": 0}], NOW))


class SkyTests(unittest.TestCase):
    """The moon and whether tonight is worth looking up."""

    def setUp(self):
        self.data = report.build_report(snapshot(), sources.Config(), now=NOW)
        self.sky = self.data["sky"]

    def test_the_phase_is_named(self):
        self.assertEqual(self.sky["phase"], "waning-crescent")
        self.assertEqual(self.sky["name"], "Φθίνουσα Ημισέληνος")
        self.assertEqual(self.sky["emoji"], "🌘")

    def test_illumination_matches_the_phase(self):
        # 0.887 of the way round the month is a thin crescent, about 12% lit.
        self.assertAlmostEqual(self.sky["illumination"], 0.121, places=2)

    def test_rise_and_set_are_formatted(self):
        self.assertRegex(self.sky["rise"], r"^\d{2}:\d{2}$")
        self.assertRegex(self.sky["set"], r"^\d{2}:\d{2}$")

    def test_a_clear_moonless_night_is_ideal(self):
        self.assertEqual(greek.stargazing_level(5, 20, 0.05)[0], "ideal")

    def test_a_clear_full_moon_is_not_ideal(self):
        level = greek.stargazing_level(5, 20, 0.99)[0]
        self.assertEqual(level, "moonlit")

    def test_overcast_reads_as_no_chance(self):
        self.assertEqual(greek.stargazing_level(95, 20, 0.1)[0], "none")

    def test_hazy_air_rules_out_ideal(self):
        self.assertNotEqual(greek.stargazing_level(5, 80, 0.05)[0], "ideal")

    def test_missing_phase_means_no_row(self):
        forecast = fixtures.forecast()
        forecast["daily"].pop("moon_phase")
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        self.assertIsNone(data["sky"])

    def test_the_hero_moon_is_the_real_one(self):
        """A stock crescent in the hero above a card reading Πανσέληνος would
        be the page contradicting itself."""
        forecast = fixtures.forecast()
        forecast["daily"]["moon_phase"] = [0.5] + [0.5] * 6   # full
        forecast["current"]["is_day"] = 0
        forecast["current"]["weather_code"] = 0
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        self.assertEqual(data["current"]["emoji"], "🌕")
        self.assertEqual(data["sky"]["emoji"], "🌕")

    def test_a_cloudy_night_keeps_its_weather_icon(self):
        forecast = fixtures.forecast()
        forecast["daily"]["moon_phase"] = [0.5] + [0.5] * 6
        forecast["current"]["is_day"] = 0
        forecast["current"]["weather_code"] = 3          # overcast
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        self.assertNotEqual(data["current"]["emoji"], "🌕")

    def test_a_clear_day_shows_the_sun(self):
        forecast = fixtures.forecast()
        forecast["daily"]["moon_phase"] = [0.5] + [0.5] * 6
        forecast["current"]["is_day"] = 1
        forecast["current"]["weather_code"] = 0
        data = report.build_report(snapshot(forecast=forecast),
                                   sources.Config(), now=NOW)
        self.assertNotEqual(data["current"]["emoji"], "🌕")


class NormalTests(unittest.TestCase):
    """Today against the decade's average for the same date."""

    def _normal(self):
        data = report.build_report(snapshot(), sources.Config(), now=NOW)
        return data["normal"]

    def test_it_compares_against_the_same_date(self):
        """Not against yesterday and not against the month: the archive holds
        whole years, so the value is one calendar day averaged over ten."""
        normal = self._normal()
        self.assertIsNotNone(normal)
        self.assertEqual(normal["years"], 10)
        self.assertAlmostEqual(normal["today"] - normal["value"],
                               normal["delta"], places=1)

    def test_a_warm_day_reads_as_warmer(self):
        normal = report.build_normal(
            fixtures.normals(), {"iso": "2026-10-06", "min": 20.0, "max": 24.0})
        self.assertTrue(normal["warmer"])
        self.assertIn("θερμότερα", normal["text"])
        self.assertAlmostEqual(normal["today"], 22.0, places=1)

    def test_a_cold_day_reads_as_colder(self):
        normal = report.build_normal(
            fixtures.normals(), {"iso": "2026-10-06", "min": 2.0, "max": 4.0})
        self.assertFalse(normal["warmer"])
        self.assertIn("ψυχρότερα", normal["text"])

    def test_a_typical_day_says_so(self):
        # Read the normal rather than guessing it, so the fixture's seasonal
        # curve can change without silently turning this into a cold-day test.
        value = self._normal()["value"]
        typical = report.build_normal(
            fixtures.normals(),
            {"iso": "2026-10-06", "min": value, "max": value})
        self.assertIn("κανονικά", typical["text"])

    def test_too_few_years_is_not_a_normal(self):
        thin = {"daily": {"time": ["2024-10-06"], "temperature_2m_mean": [14.0]}}
        self.assertIsNone(report.build_normal(
            thin, {"iso": "2026-10-06", "min": 10.0, "max": 12.0}))

    def test_missing_normals_are_not_an_error(self):
        for broken in (None, {}, {"daily": {}}):
            data = report.build_report(snapshot(normals=broken),
                                       sources.Config(), now=NOW)
            self.assertIsNone(data["normal"])


class SnowTests(unittest.TestCase):
    """Snow on the mountains. It was its own card once and hid itself out of
    season; it now feeds the mountain card, which needs it in July too."""

    def _snow(self, local_only=True, **kwargs):
        data = report.build_report(snapshot(snow=fixtures.snow(**kwargs)),
                                   sources.Config(), now=NOW)
        return (data.get("mountain") or {}).get("depth"), data

    def test_a_green_winter_still_reports_zero_depth(self):
        """The old card hid itself; the mountain card cannot, because the
        summer half needs the same forecast."""
        depth, data = self._snow()
        self.assertEqual(depth, 0.0)
        self.assertIsNotNone(data["mountain"])

    def test_a_fall_reaches_the_card(self):
        depth, data = self._snow(fall=[3.0, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertIsNotNone(data["mountain"])
        # The pass's own total, not the sum across both points: the card names
        # one place, and adding a second location would inflate it.
        self.assertAlmostEqual(data["mountain"]["fall"], 8.0, places=1)
        self.assertEqual(data["mountain"]["season"], "winter")

    def test_depth_is_converted_to_centimetres(self):
        # snow_depth is metres of water equivalent; 0.10 m becomes 10 cm.
        depth, _ = self._snow(depth=[0.10, 0.10, 0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(depth, 10.0, places=1)

    def test_missing_snow_data_is_not_an_error(self):
        data = report.build_report(snapshot(snow=None),
                                   sources.Config(), now=NOW)
        self.assertIsNone(data["mountain"])


class StationTests(unittest.TestCase):
    """The one measured number on the page, which is also the most fragile."""

    # The fixture's stamps are UTC; production resolves the zone from the
    # forecast payload, and a stock Windows Python has no tzdata of its own.
    TZ = datetime.timezone(datetime.timedelta(hours=3))
    # 13:10 UTC is 16:10 in Florina in summer, so now sits just after it.
    NOW = datetime.datetime(2026, 10, 6, 16, 30)

    def _station(self, rows=None, **config):
        payload = fixtures.station(rows) if rows is not None else fixtures.station()
        if config.pop("empty", False):
            payload = []
        cfg = sources.Config(**config) if config else sources.Config()
        found = report.build_stations({"station": payload}, cfg, self.NOW,
                                      tz=self.TZ)
        if not found:
            return None
        return found["all"][0]

    def test_the_slashes_mean_no_sensor_not_zero(self):
        """EMY writes missing readings as a run of slashes of varying length.
        Reading those as 0 would put a fabricated frost or a fabricated calm
        on the page."""
        for value in ("/", "///", "/////", "", "NaN", None, "  "):
            self.assertIsNone(report._reading(value), repr(value))
        self.assertEqual(report._reading("13.8"), 13.8)
        self.assertEqual(report._reading("-1.6"), -1.6)
        self.assertEqual(report._reading("0.0"), 0.0)

    def test_a_reading_carries_its_own_time(self):
        """It lags upstream, so the age travels with it rather than being
        passed off as the current conditions."""
        station = self._station()
        self.assertEqual(station["observed"], "16:10")
        self.assertEqual(station["age_minutes"], 20)
        self.assertIn("20", station["age_text"])

    def test_the_stamp_is_utc_not_local(self):
        """The field carries no zone and is UTC. Reading it as local made
        every station look three hours older than it was and printed the
        wrong time — checked against EMY's own portal, whose AUTO reports
        end in Z, and against the diurnal cycle, which only lines up with a
        local-time model after a three-hour shift."""
        converted = report._station_time("202610061310", self.TZ)
        self.assertEqual(converted.strftime("%H:%M"), "16:10")
        # Without a zone it stays UTC, which is what the arithmetic needs.
        self.assertEqual(report._station_time("202610061310").strftime("%H:%M"),
                         "13:10")

    def test_it_gives_the_stations_own_day(self):
        station = self._station()
        self.assertEqual(station["samples"], 3)
        self.assertAlmostEqual(station["day_min"], 11.0, places=1)
        self.assertAlmostEqual(station["day_max"], 13.8, places=1)

    def test_a_stale_reading_is_dropped_rather_than_shown_as_current(self):
        # One station in the network is a month behind.
        old = [fixtures._row("202609080335", "14.4")]
        self.assertIsNone(self._station(old))

    def test_a_reading_slightly_in_the_future_is_tolerated(self):
        # Clocks disagree; an hour of skew is not a reason to hide the card.
        rows = [fixtures._row("202610061410", "13.8")]
        self.assertIsNotNone(self._station(rows))

    def test_a_reading_far_in_the_future_is_not(self):
        rows = [fixtures._row("202610070900", "13.8")]
        self.assertIsNone(self._station(rows))

    def test_a_station_can_be_switched_off(self):
        self.assertIsNone(self._station(empty=True))

    def test_missing_arguments_are_not_an_error(self):
        cfg = sources.Config()
        for snapshot in ({}, {"station": None}, {"station": {}},
                         {"station": []}, {"station": [{}]},
                         {"station": [{"records": []}]}):
            self.assertIsNone(report.build_stations(snapshot, cfg, self.NOW))

    def test_a_broken_timestamp_is_skipped(self):
        rows = [fixtures._row("not-a-stamp", "13.8")]
        self.assertIsNone(self._station(rows))
        rows = [fixtures._row("2026100613", "13.8")]
        self.assertIsNone(self._station(rows))

    def test_a_missing_temperature_hides_the_card(self):
        rows = [fixtures._row("202610061310", "/////")]
        self.assertIsNone(self._station(rows))


class FlorinaStationTests(unittest.TestCase):
    """The one HTML source, and the only station actually in the town."""

    # Production resolves this from the forecast payload; a stock Windows
    # Python has no tzdata, so the tests supply it directly.
    TZ = datetime.timezone(datetime.timedelta(hours=3))
    NOW = datetime.datetime(2026, 10, 7, 15, 10)

    def _parse(self, html=None, now=None, **config):
        payload = {"html": fixtures.emy_florina() if html is None else html}
        cfg = sources.Config(**config) if config else sources.Config()
        return report.parse_florina(payload, now or self.NOW, cfg, tz=self.TZ)

    def test_the_auto_report_is_parsed(self):
        reading = self._parse()
        self.assertIsNotNone(reading)
        self.assertEqual(reading["name"], "Φλώρινα")
        self.assertEqual(reading["temp"], 24.0)
        self.assertEqual(reading["pressure"], 1021.0)
        self.assertEqual(reading["samples"], 4)

    def test_the_stamp_is_utc_and_is_shown_in_local_time(self):
        """The report says 12:00Z, which is 15:00 in Florina in summer. Reading
        it as local would make every reading look three hours old."""
        reading = self._parse()
        self.assertEqual(reading["observed"], "15:00")
        self.assertEqual(reading["age_minutes"], 10)

    def test_humidity_is_computed_from_the_dew_point(self):
        """24 C with a 1 C dew point is dry; the physics is not scraped."""
        reading = self._parse()
        self.assertGreater(reading["humidity"], 15)
        self.assertLess(reading["humidity"], 30)

    def test_below_zero_temperatures_keep_their_sign(self):
        """`M00` is minus zero in a SYNOP report, and Florina has real frost."""
        reports = [("071200Z", "00000KT", "M03", "M05", "1023")]
        reading = self._parse(fixtures.emy_florina(reports))
        self.assertEqual(reading["temp"], -3.0)

    def test_a_variable_wind_has_no_direction(self):
        reading = self._parse()
        self.assertIsNone(reading["wind_dir_text"])
        self.assertIsNotNone(reading["wind"])

    def test_a_numeric_wind_gets_a_direction(self):
        reports = [("071200Z", "24005KT", "23", "02", "1022"),
                   ("071130Z", "24025KT", "23", "02", "1022"),
                   ("071100Z", "VRB03MPS", "23", "02", "1022")]
        reading = self._parse(fixtures.emy_florina(reports))
        self.assertIsNotNone(reading["wind_dir_text"])
        # 5 knots is about 9 km/h.
        self.assertAlmostEqual(reading["wind"], 9.3, places=1)

    def test_the_latest_report_wins(self):
        reading = self._parse()
        self.assertEqual(reading["temp"], 24.0)      # the 12:00 one

    def test_a_stale_page_is_dropped(self):
        late = datetime.datetime(2026, 10, 9, 15, 10)
        self.assertIsNone(self._parse(now=late))

    def test_a_page_with_no_reports_yields_nothing(self):
        self.assertIsNone(self._parse("<html><body>maintenance</body></html>"))

    def test_the_source_can_be_switched_off(self):
        self.assertIsNone(self._parse(florina_enabled=False))

    @staticmethod
    def _references():
        # UTC, so these land at 14:30, 14:15 and 14:00 local — comfortably
        # before the test's 15:10 now, which the staleness guard requires.
        rows = [fixtures._row("202610071130", "13.8"),
                fixtures._row("202610071115", "13.4"),
                fixtures._row("202610071100", "13.0")]
        return fixtures.station(rows)

    def test_the_town_leads_and_the_rest_become_references(self):
        snapshot = {"station": self._references(), "florina": {
            "html": fixtures.emy_florina()}}
        built = report.build_stations(snapshot, sources.Config(), self.NOW,
                                      tz=self.TZ)
        self.assertTrue(built["town"])
        self.assertEqual(built["primary"]["name"], "Φλώρινα")
        self.assertEqual(len(built["all"]), 2)
        self.assertFalse(built["all"][1]["primary"])

    def test_without_the_town_it_falls_back_to_the_references(self):
        built = report.build_stations({"station": self._references()},
                                      sources.Config(), self.NOW, tz=self.TZ)
        self.assertFalse(built["town"])
        self.assertEqual(built["primary"]["name"], "Καστοριά")


class SnowClimateTests(unittest.TestCase):
    """How often it snows, and how that changed. Always visible, because a
    climatology means something in July."""

    JAN = datetime.datetime(2026, 1, 15, 9, 0)
    JUL = datetime.datetime(2026, 7, 15, 9, 0)

    def test_it_is_available_in_every_month(self):
        for month in range(1, 13):
            card = report.build_snow_climatology(
                datetime.datetime(2026, month, 15, 9, 0))
            self.assertIsNotNone(card, "missing in month %d" % month)

    def test_it_marks_the_current_month_only_in_winter(self):
        self.assertEqual(report.build_snow_climatology(self.JAN)["current"], 1)
        self.assertIsNone(report.build_snow_climatology(self.JUL)["current"])

    def test_the_winter_months_are_the_four_that_matter(self):
        card = report.build_snow_climatology(self.JAN)
        self.assertEqual([m["month"] for m in card["months"]], [12, 1, 2, 3])

    def test_every_month_got_less_snowy(self):
        """The finding the card exists to report."""
        card = report.build_snow_climatology(self.JAN)
        for month in card["months"]:
            self.assertLess(month["now"], month["before"],
                            "%s did not decline" % month["name"])

    def test_january_is_the_biggest_change(self):
        card = report.build_snow_climatology(self.JAN)
        drops = {m["month"]: m["before"] - m["now"] for m in card["months"]}
        self.assertEqual(max(drops, key=drops.get), 1)

    def test_the_headline_counts_halved(self):
        card = report.build_snow_climatology(self.JAN)
        self.assertEqual(card["days_before"], 60)
        self.assertEqual(card["days_now"], 24)
        self.assertLess(card["days_now"], card["days_before"] * 0.5)

    def test_the_decade_series_falls(self):
        card = report.build_snow_climatology(self.JAN)
        decades = [d["days"] for d in card["decades"]]
        self.assertEqual(len(decades), 9)
        self.assertGreater(decades[0], decades[-1])
        # Monotonic would be too strong — 1950s dip below the 1960s — but the
        # trend has to be downward overall.
        self.assertGreater(sum(decades[:3]) / 3, sum(decades[-3:]) / 3)

    def test_the_peak_is_usable_for_scaling(self):
        card = report.build_snow_climatology(self.JAN)
        self.assertGreaterEqual(card["peak"],
                                max(m["before"] for m in card["months"]))
        self.assertGreaterEqual(card["decade_peak"],
                                max(d["days"] for d in card["decades"]))

    def test_the_note_says_fewer_not_smaller(self):
        card = report.build_snow_climatology(self.JAN)
        self.assertIn("Λιγότερες", card["note"])
        self.assertIn("όχι μικρότερες", card["note"])


class MountainCardTests(unittest.TestCase):
    """One card, two seasons, one forecast. The station on the ridge is real
    but its feed batches a day or more behind, so conditions come from the
    model and the station is used for validation instead."""

    OCT = datetime.datetime(2026, 10, 6, 21, 59)
    JAN = datetime.datetime(2027, 1, 15, 9, 0)

    def _card(self, now, snow=None, **over):
        payload = {'forecast': fixtures.forecast(), 'air': fixtures.air(),
                   'history': fixtures.daily_history(),
                   'terrain': fixtures.terrain(), 'normals': fixtures.normals(),
                   'snow': snow if snow is not None else fixtures.snow(),
                   'station': fixtures.station(), 'alerts': fixtures.alerts(),
                   'ages': {}, 'errors': {}, 'stale': False}
        payload.update(over)
        return report.build_report(payload, sources.Config(), now=now)

    @staticmethod
    def _winter():
        return fixtures.snow(fall=[8.0, 4.0, 2.0, 0, 0, 0, 0],
                             depth=[0.45, 0.50, 0.52, 0, 0, 0, 0],
                             hourly_temp=-3.0, hourly_precip=0.8,
                             freezing=1200, start=datetime.datetime(2027, 1, 15))

    def test_summer_mode_out_of_season(self):
        card = self._card(self.OCT)["mountain"]
        self.assertEqual(card["season"], "summer")
        self.assertEqual(card["emoji"], "🥾")
        self.assertEqual(card["label"], "Καλοκαιρινή απόδραση")

    def test_winter_mode_in_january(self):
        card = self._card(self.JAN, self._winter())["mountain"]
        self.assertEqual(card["season"], "winter")
        self.assertEqual(card["emoji"], "🎿")

    def test_snow_overrides_the_month(self):
        """An unseasonal fall in October should still switch the card over."""
        early = fixtures.snow(depth=[0.10] * 7, hourly_temp=-1.0,
                              start=datetime.datetime(2026, 10, 6))
        card = self._card(self.OCT, early)["mountain"]
        self.assertEqual(card["season"], "winter")

    def test_the_gap_is_the_city_minus_the_mountain(self):
        card = self._card(self.OCT)["mountain"]
        self.assertAlmostEqual(card["gap"],
                               card["city_temp"] - card["temp"], places=1)
        self.assertIn("πιο δροσερά", card["gap_text"])

    def test_a_missing_city_reading_does_not_break_the_hint(self):
        four = fixtures.snow(hourly_temp=14.0,
                             start=datetime.datetime(2026, 10, 6))
        card = self._card(self.OCT, four)["mountain"]
        self.assertIsNotNone(card)
        self.assertIsNotNone(card["temp"])
        self.assertTrue(card["hint"])

    def test_winter_mode_carries_the_road(self):
        card = self._card(self.JAN, self._winter())["mountain"]
        self.assertEqual(card["road_key"], "closed")
        self.assertIn("αλυσίδες", card["hint"])
        self.assertEqual(card["depth"], 52.0)

    def test_the_road_and_snow_cards_are_gone(self):
        """They were merged into this one, so the local block must not still
        carry them or the page shows the same thing twice."""
        data = self._card(self.JAN, self._winter())
        local = data.get("local") or {}
        self.assertNotIn("road", local)
        self.assertNotIn("snow", local)

    def test_no_mountain_data_is_not_an_error(self):
        data = self._card(self.OCT, snow=[])
        self.assertIsNone(data["mountain"])

    def test_the_hours_start_now_not_midnight(self):
        """Risk counted from midnight made a clearing day look like a blizzard
        and a clearing evening look calm when snow was still coming."""
        snow = fixtures.snow(start=datetime.datetime(2026, 10, 6))
        built = report.build_snow({"snow": snow}, sources.Config(), self.OCT)
        first = built["hours"][0]
        self.assertTrue(first["now"])
        self.assertTrue(first["iso"].startswith("2026-10-06T21"))


class NormalBiasTests(unittest.TestCase):
    """The 'normal' baseline is ERA5, which runs warm at Florina, so it is
    calibrated against fourteen years of measured days from a station in the
    town. Without the correction almost every day read colder than it was."""

    def test_the_table_covers_every_month(self):
        self.assertEqual(len(report.ERA5_BIAS_MONTHLY), 13)
        for month in range(1, 13):
            self.assertLess(abs(report.ERA5_BIAS_MONTHLY[month]), 2.0)

    def test_known_months_match_the_measurement(self):
        # Measured minus ERA5, from 5097 days at Florina, 2010-2023.
        self.assertAlmostEqual(report.era5_bias(8), -1.39, places=2)
        self.assertAlmostEqual(report.era5_bias(1), -0.97, places=2)
        self.assertAlmostEqual(report.era5_bias(2), 0.33, places=2)

    def test_most_months_are_too_warm_in_era5(self):
        """The direction matters: a warm baseline makes ordinary days look
        cool for their date."""
        too_warm = [m for m in range(1, 13) if report.era5_bias(m) < 0]
        self.assertEqual(len(too_warm), 9)
        for month in (2, 3, 4):
            self.assertNotIn(month, too_warm)

    def test_out_of_range_months_are_not_an_error(self):
        for bad in (0, 13, -1, None, "", "August"):
            self.assertEqual(report.era5_bias(bad), 0.0)

    def test_the_normal_is_the_raw_mean_plus_the_correction(self):
        normals = fixtures.normals()
        raw = [v for s, v in zip(normals["daily"]["time"],
                                 normals["daily"]["temperature_2m_mean"])
               if s[5:] == "08-15" and v is not None]
        expected = sum(raw) / len(raw) - 1.39
        result = report.build_normal(
            normals, {"iso": "2026-08-15", "mean": 24.0})
        self.assertAlmostEqual(result["value"], expected, places=1)
        self.assertAlmostEqual(result["bias"], -1.39, places=2)

    def test_the_delta_still_reconciles(self):
        result = report.build_normal(
            fixtures.normals(), {"iso": "2026-01-20", "mean": 5.0})
        self.assertAlmostEqual(result["value"],
                               result["today"] - result["delta"], places=1)

    def test_the_quoted_range_still_brackets_the_value(self):
        result = report.build_normal(
            fixtures.normals(), {"iso": "2026-08-15", "mean": 24.0})
        low, high = result["range"]
        self.assertLessEqual(low, result["value"])
        self.assertLessEqual(result["value"], high)


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
