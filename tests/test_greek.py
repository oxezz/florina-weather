# -*- coding: utf-8 -*-
"""Unit tests for the Greek vocabulary layer. No network, no clock."""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import greek  # noqa: E402


class DescribeTests(unittest.TestCase):

    def test_every_known_code_has_real_text(self):
        for code in greek._WMO:
            text = greek.describe(code)
            self.assertTrue(text and text != greek.UNKNOWN_TEXT,
                            "code %s has no description" % code)

    def test_unknown_code_falls_back(self):
        self.assertEqual(greek.describe(1234), greek.UNKNOWN_TEXT)
        self.assertEqual(greek.describe(None), greek.UNKNOWN_TEXT)
        self.assertEqual(greek.describe("nonsense"), greek.UNKNOWN_TEXT)

    def test_known_translations(self):
        # These are the ones the previous version got wrong.
        self.assertEqual(greek.describe(3), "Νεφοσκεπής")       # was "Λίγες νεφώσεις"
        self.assertEqual(greek.describe(48), "Παγωμένη ομίχλη")  # was "Μάγκρα ομίχλη"
        self.assertEqual(greek.describe(65), "Ισχυρή βροχή")     # was "Χαλαρά βροχή"
        self.assertEqual(greek.describe(66), "Ασθενής παγωμένη βροχή")  # was a thunderstorm
        self.assertEqual(greek.describe(75), "Ισχυρή χιονόπτωση")  # was "Χαλαρά χιόνι"
        self.assertEqual(greek.describe(82), "Ισχυρές τοπικές βροχές")

    def test_newly_added_codes(self):
        for code in (56, 57, 77):
            self.assertIn(code, greek._WMO)
            self.assertNotEqual(greek.describe(code), greek.UNKNOWN_TEXT)

    def test_emoji_are_assigned_and_distinct_where_it_matters(self):
        for code in greek._WMO:
            day = greek.emoji(code, True)
            night = greek.emoji(code, False)
            self.assertTrue(day, "code %s has no day emoji" % code)
            self.assertTrue(night, "code %s has no night emoji" % code)

    def test_partly_cloudy_is_not_a_snowman(self):
        # U+26C4 is a snowman; the old table used it for "partly cloudy".
        self.assertNotIn("\u26c4", greek.emoji(2, True))
        self.assertNotIn("\u26c4", greek.emoji(2, False))

    def test_fog_emoji_is_not_a_draughts_piece(self):
        # U+26C0/U+26CB are draughts pieces / unassigned.
        for code in (45, 48):
            self.assertNotIn("\u26c0", greek.emoji(code, True))
            self.assertNotIn("\u26cb", greek.emoji(code, True))

    def test_thunderstorm_uses_the_thunder_cloud(self):
        self.assertIn("\u26c8", greek.emoji(95, True))

    def test_night_variant_for_clear_sky(self):
        self.assertEqual(greek.emoji(0, True), "\u2600\ufe0f")
        self.assertNotEqual(greek.emoji(0, False), greek.emoji(0, True))


class WindTests(unittest.TestCase):

    def test_compass_points(self):
        expected = {0: "Β", 45: "ΒΑ", 90: "Α", 135: "ΝΑ",
                    180: "Ν", 225: "ΝΔ", 270: "Δ", 315: "ΒΔ"}
        for degrees, name in expected.items():
            self.assertEqual(greek.compass(degrees), name, "at %s°" % degrees)

    def test_compass_was_previously_wrong(self):
        # The old table had ΑΝ/ΝΝ/ΔΔ at 135/225/315.
        self.assertEqual(greek.compass(135), "ΝΑ")
        self.assertEqual(greek.compass(225), "ΝΔ")
        self.assertEqual(greek.compass(315), "ΒΔ")

    def test_compass_wraps_and_survives_junk(self):
        self.assertEqual(greek.compass(360), "Β")
        self.assertEqual(greek.compass(-45), "ΒΔ")
        self.assertEqual(greek.compass(359.9), "Β")
        self.assertEqual(greek.compass(None), "Β")
        self.assertEqual(greek.compass("oops"), "Β")

    def test_arrow_shows_where_the_wind_blows_to(self):
        self.assertEqual(greek.wind_arrow(0), "\u2193")    # from N -> blows S
        self.assertEqual(greek.wind_arrow(90), "\u2190")   # from E -> blows W
        self.assertEqual(greek.wind_arrow(180), "\u2191")  # from S -> blows N
        self.assertEqual(greek.wind_arrow(270), "\u2192")  # from W -> blows E

    def test_beaufort_bands(self):
        cases = [(0, 0), (0.9, 0), (1, 1), (5, 1), (6, 2), (11, 2), (12, 3),
                 (19, 3), (20, 4), (28, 4), (29, 5), (38, 5), (39, 6),
                 (49, 6), (50, 7), (61, 7), (62, 8), (74, 8), (75, 9),
                 (88, 9), (89, 10), (102, 10), (103, 11), (117, 11), (118, 12),
                 (250, 12)]
        for kmh, expected in cases:
            self.assertEqual(greek.beaufort(kmh), expected, "%s km/h" % kmh)

    def test_beaufort_survives_junk(self):
        self.assertEqual(greek.beaufort(None), 0)
        self.assertEqual(greek.beaufort(-5), 0)
        self.assertEqual(greek.beaufort("x"), 0)
        self.assertEqual(greek.beaufort_text(8), "Θυελλώδης")
        self.assertEqual(greek.beaufort_text(99), "Τυφώνας")


class LevelTests(unittest.TestCase):

    def test_uv_levels(self):
        self.assertEqual(greek.uv_level(0)[0], "Χαμηλός")
        self.assertEqual(greek.uv_level(2.9)[0], "Χαμηλός")
        self.assertEqual(greek.uv_level(3)[0], "Μέτριος")
        self.assertEqual(greek.uv_level(5.35)[0], "Μέτριος")
        self.assertEqual(greek.uv_level(7)[0], "Υψηλός")
        self.assertEqual(greek.uv_level(9)[0], "Πολύ υψηλός")
        self.assertEqual(greek.uv_level(12)[0], "Ακραίος")
        self.assertEqual(greek.uv_level(None)[0], "—")

    def test_aqi_levels(self):
        self.assertEqual(greek.aqi_level(10)[0], "Καλή")
        self.assertEqual(greek.aqi_level(31)[0], "Αποδεκτή")
        self.assertEqual(greek.aqi_level(50)[0], "Μέτρια")
        self.assertEqual(greek.aqi_level(70)[0], "Κακή")
        self.assertEqual(greek.aqi_level(90)[0], "Πολύ κακή")
        self.assertEqual(greek.aqi_level(150)[0], "Εξαιρετικά κακή")

    def test_visibility_text(self):
        self.assertEqual(greek.visibility_text(41220), "Καθαρή ατμόσφαιρα")
        self.assertEqual(greek.visibility_text(500), "Πολύ μειωμένη ορατότητα")
        self.assertEqual(greek.visibility_text(None), "—")


class TimeTests(unittest.TestCase):

    def test_format_hhmm_strips_the_date(self):
        # The old template printed the whole ISO string as the sunrise time.
        self.assertEqual(greek.format_hhmm("2026-10-06T07:35"), "07:35")
        self.assertEqual(greek.format_hhmm("07:35"), "07:35")
        self.assertEqual(greek.format_hhmm(""), "—")
        self.assertEqual(greek.format_hhmm(None), "—")
        self.assertEqual(greek.format_hhmm("nonsense"), "—")

    def test_format_duration(self):
        self.assertEqual(greek.format_duration(41571), "11ω 32λ")
        self.assertEqual(greek.format_duration(3600), "1ω")
        self.assertEqual(greek.format_duration(90), "1λ")
        self.assertEqual(greek.format_duration(None), "—")
        self.assertEqual(greek.format_duration(-4), "—")

    def test_day_labels(self):
        today = datetime.date(2026, 10, 6)
        self.assertEqual(greek.day_label(today, today), "Σήμερα")
        self.assertEqual(greek.day_label(today + datetime.timedelta(days=1), today), "Αύριο")
        self.assertEqual(greek.day_label(today + datetime.timedelta(days=2), today), "Πέμπτη")

    def test_long_date(self):
        self.assertEqual(greek.long_date(datetime.date(2026, 10, 6)), "Τρίτη 6 Οκτωβρίου")

    def test_weekday_names_cover_the_week(self):
        monday = datetime.date(2026, 10, 5)
        for offset in range(7):
            day = monday + datetime.timedelta(days=offset)
            self.assertTrue(greek.weekday_name(day))
            self.assertTrue(greek.weekday_short(day))
        self.assertEqual(greek.weekday_name(monday), "Δευτέρα")


class WarningTests(unittest.TestCase):

    def test_level_from_awareness_parameter(self):
        self.assertEqual(greek.warning_level("2; Yellow; Moderate")[0], "yellow")
        self.assertEqual(greek.warning_level("3; Orange; Severe")[0], "orange")
        self.assertEqual(greek.warning_level("4; Red; Extreme")[0], "red")
        self.assertEqual(greek.warning_level("1; Green; Minor")[0], "green")

    def test_level_from_numeric_only(self):
        self.assertEqual(greek.warning_level("2")[0], "yellow")
        self.assertEqual(greek.warning_level("4")[0], "red")

    def test_level_falls_back_to_severity(self):
        self.assertEqual(greek.warning_level(None, "Severe")[0], "orange")
        self.assertEqual(greek.warning_level(None, "Extreme")[0], "red")
        self.assertEqual(greek.warning_level(None, "Moderate")[0], "yellow")
        self.assertEqual(greek.warning_level(None, None)[0], "yellow")

    def test_hazards(self):
        self.assertEqual(greek.hazard("Wind")[0], "Άνεμος")
        self.assertEqual(greek.hazard("Rain")[0], "Βροχή")
        self.assertEqual(greek.hazard("Thunderstorm")[0], "Καταιγίδες")
        self.assertEqual(greek.hazard("Snow-Ice")[0], "Χιόνι και πάγος")
        self.assertTrue(greek.hazard(None)[0])

    def test_warning_rank_orders_by_severity(self):
        self.assertLess(greek.warning_rank("green"), greek.warning_rank("yellow"))
        self.assertLess(greek.warning_rank("yellow"), greek.warning_rank("orange"))
        self.assertLess(greek.warning_rank("orange"), greek.warning_rank("red"))


class NumberTests(unittest.TestCase):

    def test_number(self):
        self.assertEqual(greek.number(11.5), "12")
        self.assertEqual(greek.number(11.5, 1), "11.5")
        self.assertEqual(greek.number(0), "0")
        self.assertEqual(greek.number(None), "—")
        self.assertEqual(greek.number("abc"), "—")
        self.assertEqual(greek.number(float("nan")), "—")
        self.assertEqual(greek.number(float("inf")), "—")

    def test_number_custom_dash(self):
        self.assertEqual(greek.number(None, 0, "n/a"), "n/a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
