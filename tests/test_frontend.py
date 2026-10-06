# -*- coding: utf-8 -*-
"""Static consistency checks between the server, the template and app.js.

These catch the classic failure mode of a split front-end: an id renamed in
one file and not the other, which no Python test would otherwise notice.
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app  # noqa: E402
import sources  # noqa: E402


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


# Placeholders app.py knows how to substitute.
KNOWN_PLACEHOLDERS = {"TITLE", "PLACE", "REGION", "REFRESH", "VERSION"}


class TemplateTests(unittest.TestCase):

    def setUp(self):
        self.template = read("template.html")

    def test_every_placeholder_is_supported(self):
        found = set(re.findall(r"\{\{([A-Z_]+)\}\}", self.template))
        self.assertTrue(found)
        self.assertEqual(found - KNOWN_PLACEHOLDERS, set(),
                         "template uses placeholders app.py cannot fill")

    def test_renders_without_leftovers(self):
        rendered = app.ShellCache(app.TEMPLATE).render(sources.Config())
        self.assertNotIn("{{", rendered)
        self.assertIn("Φλώρινα", rendered)
        self.assertIn("Δυτική Μακεδονία", rendered)

    def test_placeholders_are_html_escaped(self):
        config = sources.Config(place='<script>alert("x")</script>')
        rendered = app.ShellCache(app.TEMPLATE).render(config)
        self.assertNotIn("<script>alert", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_script_and_stylesheet_are_referenced(self):
        self.assertIn("/app.js", self.template)
        self.assertIn("/style.css", self.template)

    def test_language_is_greek(self):
        self.assertIn('lang="el"', self.template)

    def test_theme_bootstrap_runs_before_the_body(self):
        # Otherwise a light-mode user sees a dark flash on every load.
        head, _, rest = self.template.partition("<body")
        self.assertIn("/theme.js", head)
        self.assertIn("/app.js", rest)

    def test_all_three_appearance_modes_are_offered(self):
        for mode in ("auto", "light", "dark"):
            self.assertIn('data-mode="%s"' % mode, self.template)


class GreekLabelTests(unittest.TestCase):
    """Guards the wording that was reviewed by hand."""

    def setUp(self):
        self.template = read("template.html")
        self.js = read("app.js")

    def test_gust_label_names_the_quantity(self):
        self.assertIn("Ριπές ανέμου", self.template)
        # "ριπή ανέμου" is a gust; "ροή ανέμου" would be a wind *flow*.
        self.assertNotIn("ροές", self.template.lower())
        self.assertNotIn("ροές", self.js.lower())

    def test_feels_like_label_is_a_noun(self):
        self.assertIn(">Αίσθηση<", self.template)

    def test_precipitation_card_uses_the_generic_term(self):
        # The card shows rain + showers + snow, so "Βροχή" would be wrong.
        self.assertIn(">Υετός<", self.template)

    def test_footer_abbreviates_seconds_in_greek(self):
        self.assertIn("δευτ.", self.template)
        self.assertNotIn("{{REFRESH}}s", self.template)
        self.assertNotIn('"s")', self.js)


class ClientTests(unittest.TestCase):

    def setUp(self):
        self.js = read("app.js")
        self.template = read("template.html")

    def used_ids(self):
        ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', self.js))
        ids |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', self.js))
        return ids

    def test_every_id_the_client_looks_up_exists(self):
        declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.template))
        missing = sorted(self.used_ids() - declared)
        self.assertEqual(missing, [], "app.js looks up ids missing from template.html")

    def test_client_has_no_syntax_surprises(self):
        # A cheap sanity check that does not need Node: balanced braces.
        self.assertEqual(self.js.count("{"), self.js.count("}"))
        self.assertEqual(self.js.count("("), self.js.count(")"))
        self.assertIn('"use strict"', self.js)

    def test_client_never_assigns_html_from_data(self):
        # Upstream alert text is third-party data and must go through
        # textContent, never innerHTML.
        self.assertNotIn("innerHTML", self.js)
        self.assertNotIn("insertAdjacentHTML", self.js)
        self.assertNotIn("document.write", self.js)

    def test_client_talks_to_the_json_api(self):
        self.assertIn("/api/weather", self.js)

    def test_client_pauses_while_the_tab_is_hidden(self):
        self.assertIn("visibilitychange", self.js)
        self.assertIn("document.hidden", self.js)

    def test_client_tracks_the_pointer_for_the_glass_highlight(self):
        self.assertIn("--mx", self.js)
        self.assertIn("--my", self.js)
        self.assertIn("requestAnimationFrame", self.js)

    def test_client_leaves_light_dark_to_the_bootstrap(self):
        # The mode class belongs on <html> and is owned by theme.js.
        self.assertIn("FlorinaTheme", self.js)
        self.assertNotIn('classList.add("mode-', self.js)

    def test_one_failing_section_does_not_blank_the_page(self):
        self.assertIn("function safely(", self.js)
        self.assertIn("console.error", self.js)
        # Every renderer must go through it.
        for renderer in ("renderAlerts", "renderHero", "renderStats", "renderSun",
                         "renderChart", "renderHourly", "renderDays", "renderAir",
                         "renderStatus"):
            self.assertIn('safely(', self.js)
            self.assertIn(renderer, self.js)

    def test_client_fades_the_scrolling_hourly_edge(self):
        self.assertIn("can-right", self.js)
        self.assertIn("scrollWidth", self.js)


class ThemeBootstrapTests(unittest.TestCase):

    def setUp(self):
        self.js = read("theme.js")

    def test_reads_and_writes_a_stored_preference(self):
        self.assertIn("localStorage", self.js)
        self.assertIn("florina.mode", self.js)

    def test_applies_the_class_to_the_document_element(self):
        self.assertIn("documentElement", self.js)
        self.assertIn("mode-light", self.js)
        self.assertIn("mode-dark", self.js)

    def test_auto_follows_the_system_setting(self):
        self.assertIn("prefers-color-scheme: light", self.js)
        self.assertIn('"change"', self.js)

    def test_query_parameter_can_override_without_persisting(self):
        self.assertIn("location.search", self.js)
        self.assertIn("mode=(auto|light|dark)", self.js)

    def test_survives_storage_being_unavailable(self):
        # Private browsing throws on localStorage access.
        self.assertIn("catch", self.js)

    def test_balanced_braces(self):
        self.assertEqual(self.js.count("{"), self.js.count("}"))
        self.assertEqual(self.js.count("("), self.js.count(")"))


class StylesheetTests(unittest.TestCase):

    def setUp(self):
        self.css = read("style.css")

    def test_balanced_braces(self):
        self.assertEqual(self.css.count("{"), self.css.count("}"))

    def test_defines_the_weather_themes_the_client_uses(self):
        for theme in ("clear-day", "clear-night", "cloud", "rain", "snow", "storm", "fog"):
            self.assertIn("theme-" + theme, self.css)

    def test_defines_both_materials_for_every_weather_theme(self):
        for theme in ("clear-day", "clear-night", "cloud", "rain", "snow", "storm", "fog"):
            self.assertIn("html.mode-light body.theme-" + theme, self.css,
                          "light palette missing for " + theme)

    def test_light_and_dark_materials_are_both_defined(self):
        self.assertIn("html.mode-light", self.css)
        self.assertIn("--glass-bg", self.css)
        self.assertIn("--rim-top", self.css)

    def test_respects_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", self.css)

    def test_has_a_small_screen_breakpoint(self):
        self.assertIn("@media (max-width", self.css)

    def test_every_custom_property_used_is_defined(self):
        # Catches a typo in a var() name, which otherwise fails silently at
        # runtime by dropping the whole declaration.
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        declared |= set(re.findall(r"@property\s+(--[a-z0-9-]+)", self.css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.css))
        # These are written by app.js in response to the pointer or to data,
        # so they have no stylesheet default (var() sites supply a fallback).
        runtime = {"--c", "--level", "--mx", "--my"}
        self.assertEqual(sorted(used - declared - runtime), [],
                         "style.css uses custom properties it never defines")

    def test_light_mode_redefines_every_material_token(self):
        """The light block has to restate the dark defaults, or they leak."""
        before, separator, after = self.css.partition("html.mode-light {")
        self.assertTrue(separator, "no light material block found")
        light_block = after.split("\n}", 1)[0]
        dark = set(re.findall(r"(--[a-z0-9-]+)\s*:", before))
        light = set(re.findall(r"(--[a-z0-9-]+)\s*:", light_block))
        for token in ("--text", "--glass-bg", "--glass-border", "--rim-top", "--shadow"):
            self.assertIn(token, light, "%s not overridden for light mode" % token)
            self.assertIn(token, dark, "%s missing from the dark defaults" % token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
