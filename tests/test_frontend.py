# -*- coding: utf-8 -*-
"""Static consistency checks between the server, the template and app.js.

These catch the classic failure mode of a split front-end: an id renamed in
one file and not the other, which no Python test would otherwise notice.
"""

import json
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
KNOWN_PLACEHOLDERS = {"TITLE", "PLACE", "REGION", "REFRESH", "REFRESH_TEXT",
                      "VERSION", "SLOPE_NAME", "OG_URL", "OG_IMAGE", "DESCRIPTION"}

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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

    def test_the_inversion_hint_follows_the_configuration(self):
        """The hint used to name Florina and 1.073 m in prose, so running the
        page for another town left the explanation describing the old one."""
        config = sources.Config(place="Καστοριά", slope_name="κορυφή 1400 μ.")
        rendered = app.ShellCache(app.TEMPLATE).render(config)
        hint = rendered.split('id="hint-inversion"')[1].split("</p>")[0]
        self.assertIn("Καστοριά", hint)
        self.assertIn("κορυφή 1400 μ.", hint)
        self.assertNotIn("Φλώρινα", hint)
        self.assertNotIn("1.073", hint)

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

    def test_footer_states_the_interval_in_words(self):
        # The template carries a placeholder; app.py renders the wording, so a
        # 600 second default reads "10 λεπτά" rather than "600 δευτ.".
        self.assertIn("{{REFRESH_TEXT}}", self.template)
        self.assertNotIn("{{REFRESH}}s", self.template)
        rendered = app.ShellCache(app.TEMPLATE).render(sources.Config())
        self.assertIn("Ανανέωση κάθε 10 λεπτά", rendered)


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

    def test_client_puts_the_weather_theme_on_the_root(self):
        self.assertNotIn("document.body.className", self.js)
        self.assertIn("document.documentElement", self.js)

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

    def test_the_browser_chrome_follows_the_mode(self):
        """Without this a light page sat under a dark address bar."""
        self.assertIn("colorScheme", self.js)
        self.assertIn('meta[name="theme-color"]', self.js)

    def test_the_light_colour_matches_the_stylesheet(self):
        """The meta colour must be the value the canvas actually uses, or the
        address bar is a slightly different shade from the page."""
        css = read("style.css")
        light_base = css.split("html.mode-light")[1].split("--b1:")[1].split(";")[0].strip()
        self.assertIn(light_base, self.js,
                      "theme.js disagrees with --b1 for light mode")

    def test_the_meta_tag_exists_to_update(self):
        self.assertIn('name="theme-color"', read("template.html"))


class ServiceWorkerTests(unittest.TestCase):
    """The worker decides what a user sees when the network misbehaves."""

    def setUp(self):
        self.js = read("sw.js")

    def test_a_hanging_request_falls_back_to_cache(self):
        self.assertIn("NETWORK_TIMEOUT", self.js)
        self.assertIn("withTimeout", self.js)

    def test_navigations_share_one_cache_key(self):
        """Keying on the full URL kept a separate copy per ?mode= variant."""
        self.assertIn('networkFirst(request, "/")', self.js)

    def test_cached_responses_are_labelled(self):
        self.assertIn('headers.set("X-SW-Source", "cache")', self.js)

    def test_the_worker_still_handles_fetch(self):
        # Without a fetch handler Android Chrome never offers the install prompt.
        self.assertIn('addEventListener("fetch"', self.js)


class SkeletonTests(unittest.TestCase):
    """Placeholders shown while /api/weather is in flight."""

    def setUp(self):
        self.template = read("template.html")
        self.css = read("style.css")
        self.js = read("app.js")

    def test_the_load_placeholders_are_skeletons_not_text(self):
        self.assertNotIn("Φόρτωση", self.template)
        self.assertIn('class="skel skel-temp"', self.template)
        self.assertGreaterEqual(self.template.count('class="skel'), 10)

    def test_every_async_region_is_covered(self):
        for marker in ("skel-temp", "skel-code", "skel-value", "skel-chart",
                       "h-skel", "day-skel"):
            self.assertIn(marker, self.template, "no skeleton for " + marker)

    def test_skeletons_wait_before_appearing(self):
        """A skeleton that flashes for two frames is worse than none."""
        self.assertIn("animation: pulse 1.6s ease-in-out .35s infinite", self.css)
        self.assertIn("opacity: 0;", self.css)

    def test_reduced_motion_still_shows_the_skeleton(self):
        # Visibility comes from the animation, so it must be restored here.
        block = self.css.split("@media (prefers-reduced-motion: reduce)")[1]
        self.assertIn(".skel { opacity: .5 !important; }", block)

    def test_skeleton_colour_is_defined_for_both_materials(self):
        self.assertGreaterEqual(self.css.count("--skel-bg:"), 2)

    def test_the_client_sweeps_leftover_skeletons(self):
        self.assertIn("dropSkeletons", self.js)
        self.assertIn("data-skeleton", self.js)


class PwaTests(unittest.TestCase):
    """Installability: manifest, icons, service worker and the iOS meta tags."""

    def setUp(self):
        self.template = read("template.html")
        self.manifest = json.loads(read("manifest.webmanifest"))
        self.sw = read("sw.js")

    # -- manifest ----------------------------------------------------------

    def test_manifest_has_what_an_install_prompt_requires(self):
        for key in ("name", "short_name", "start_url", "display", "icons",
                    "background_color", "theme_color"):
            self.assertIn(key, self.manifest)
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertEqual(self.manifest["start_url"], "/")
        self.assertEqual(self.manifest["lang"], "el")

    def test_manifest_declares_both_icon_purposes(self):
        purposes = set()
        for icon in self.manifest["icons"]:
            purposes.update(icon.get("purpose", "any").split())
        self.assertIn("any", purposes)
        # Android needs a maskable icon or it letterboxes the artwork.
        self.assertIn("maskable", purposes)

    def test_manifest_offers_the_sizes_android_asks_for(self):
        sizes = {icon["sizes"] for icon in self.manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)

    def test_every_declared_icon_exists_and_is_a_real_png(self):
        for icon in self.manifest["icons"]:
            name = icon["src"].lstrip("/")
            path = os.path.join(ROOT, name)
            self.assertTrue(os.path.isfile(path), "missing icon " + name)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(8), PNG_MAGIC, name + " is not a PNG")
            self.assertLess(os.path.getsize(path), 200 * 1024, name + " is oversized")

    # -- iOS ---------------------------------------------------------------

    def test_template_links_the_manifest_and_apple_icon(self):
        self.assertIn('rel="manifest"', self.template)
        self.assertIn('rel="apple-touch-icon"', self.template)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "icon-180.png")))

    def test_template_has_the_ios_standalone_meta_tags(self):
        # Without these, Safari opens the icon in a normal browser tab.
        self.assertIn('name="apple-mobile-web-app-capable"', self.template)
        self.assertIn('name="mobile-web-app-capable"', self.template)

    # -- service worker ----------------------------------------------------

    def test_worker_handles_the_lifecycle_and_fetch(self):
        for event in ('"install"', '"activate"', '"fetch"'):
            self.assertIn(event, self.sw)
        self.assertIn("skipWaiting", self.sw)
        self.assertIn("clients.claim", self.sw)

    def test_worker_never_serves_stale_weather_from_cache_first(self):
        # Weather goes network-first; only the shell is allowed to come from
        # cache immediately.
        self.assertIn('networkFirst(request, url.origin + "/api/weather")', self.sw)
        self.assertIn('url.pathname === "/api/weather"', self.sw)
        self.assertIn("staleWhileRevalidate", self.sw)
    def test_worker_only_touches_same_origin_get_requests(self):
        self.assertIn('request.method !== "GET"', self.sw)
        self.assertIn("url.origin !== self.location.origin", self.sw)

    def test_worker_prunes_old_caches(self):
        self.assertIn("caches.delete", self.sw)
        self.assertIn("CACHE_VERSION", self.sw)

    def test_worker_ignores_non_ok_responses(self):
        self.assertIn("response.ok", self.sw)

    def test_client_registers_the_worker(self):
        js = read("app.js")
        self.assertIn('navigator.serviceWorker.register("/sw.js")', js)
        # Over https, or on loopback which browsers treat as a secure context —
        # requiring https outright would break local development.
        self.assertIn('location.protocol === "https:"', js)
        self.assertIn("127.0.0.1", js)
        self.assertIn("localhost", js)


class LocalPanelTests(unittest.TestCase):
    """The hyper-local block: frost, heating degree days, wood smoke."""

    def setUp(self):
        self.template = read("template.html")
        self.css = read("style.css")
        self.js = read("app.js")

    def test_everything_starts_hidden(self):
        # The server decides nothing here; every card hides itself when its
        # block has nothing to say, and the panel hides when all three do.
        self.assertIn('id="local-panel" hidden', self.template)
        for card in ("local-frost", "local-heating", "local-smog"):
            self.assertIn('id="%s" hidden' % card, self.template)

    def test_client_renders_each_block(self):
        for name in ("renderLocal", "renderFrost", "renderHeating", "renderSmog"):
            self.assertIn("function " + name, self.js)

    def test_client_hides_the_panel_when_no_block_applies(self):
        self.assertIn("panel.hidden = true", self.js)
        self.assertIn("shown === 0", self.js)

    def test_local_render_is_guarded_like_every_other_section(self):
        self.assertIn('safely("local"', self.js)

    def test_the_cards_share_the_row_evenly(self):
        """A lone card must fill the panel. Capping its width left a large void
        beside it on desktop, which reads as a rendering fault."""
        card = self.css.split(".local-card {")[1].split("}")[0]
        self.assertNotIn("max-width", card)
        grid = self.css.split(".local-grid {")[1].split("}")[0]
        self.assertIn("minmax(200px, 1fr)", grid)

    def test_the_tone_hairline_has_a_fallback(self):
        # --tone is set from data, so the CSS must survive its absence.
        self.assertIn("var(--tone, transparent)", self.css)

    def test_hidden_is_not_defeated_by_an_author_display_rule(self):
        """`.local-card { display: grid }` silently outranked the browser's
        `[hidden] { display: none }`, leaving both empty cards on screen with
        their loading skeletons still showing. The same bug hid inside .chip."""
        self.assertIn("[hidden] { display: none !important; }", self.css)
        self.assertIn("display: grid", self.css)  # the rule that caused it

    def test_no_explainer_targets_a_missing_id(self):
        targets = re.findall(r'aria-controls="([^"]+)"', self.template)
        declared = set(re.findall(r'id="([^"]+)"', self.template))
        self.assertTrue(targets)
        self.assertEqual(sorted(set(targets) - declared), [])


class ExplainerTests(unittest.TestCase):
    """The «?» affordance that explains the jargon-bearing metrics."""

    def setUp(self):
        self.template = read("template.html")
        self.css = read("style.css")
        self.js = read("app.js")

    def test_every_local_card_has_one(self):
        # Six cards, plus the model-agreement explainer on the 7-day panel.
        self.assertEqual(self.template.count('class="info"'), 7)
        for card in ("inversion", "road", "snow", "frost", "heating", "smog"):
            self.assertIn('aria-controls="hint-%s"' % card, self.template)
            self.assertIn('id="hint-%s" hidden' % card, self.template)
        self.assertIn('aria-controls="hint-agreement"', self.template)

    def test_they_start_collapsed_and_are_labelled(self):
        self.assertNotIn('aria-expanded="true"', self.template)
        self.assertEqual(self.template.count('aria-label="Τι σημαίνει;"'), 7)

    def test_explanations_are_written_in_greek(self):
        hints = re.findall(r'<p class="hint[^"]*"[^>]*>(.*?)</p>', self.template, re.S)
        self.assertEqual(len(hints), 7)   # six cards and the panel
        for hint in hints:
            self.assertRegex(hint, "[\\u0370-\\u03ff]",
                             "explanation is not in Greek")

    def test_the_client_wires_the_explainers(self):
        self.assertIn("function initInfo", self.js)
        self.assertIn("initInfo();", self.js)
        self.assertIn("aria-expanded", self.js)

    def test_the_smog_card_tells_pooling_from_dispersal(self):
        """High PM2.5 with a breeze is particulate blowing through, not wood
        smoke sitting on the town, and the card must say which."""
        self.assertIn("smog.pooling", self.js)
        self.assertIn("άπνοια", self.js)
        self.assertIn("διασκορπίζονται", self.js)

    def test_ventilation_advice_is_gated_on_pooling(self):
        # A "cleanest hour" suggestion is meaningless when it is blowing through.
        lines = [line for line in self.js.splitlines() if "καθαρότερος αέρας" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("smog.pooling", lines[0])

    def test_the_frost_card_shows_both_sensors(self):
        self.assertIn("frost.ground_min", self.js)
        self.assertIn("frost.air_min", self.js)
        self.assertIn("έδαφος", self.js)
        self.assertIn("αέρας", self.js)

    def test_a_thumb_can_hit_the_button(self):
        # 16px is small for a finger; the padding keeps the tap target usable
        # while the visible circle stays discreet.
        block = self.css.split(".info {")[1].split("}")[0]
        for prop in ("width", "height", "border-radius", "cursor"):
            self.assertIn(prop, block)


class HourlyStripTests(unittest.TestCase):
    """The hourly cards: snapping, and the rain-probability gauge."""

    def setUp(self):
        self.css = read("style.css")
        self.js = read("app.js")

    def test_the_strip_snaps_card_by_card(self):
        strip = self.css.split(".hourly {")[1].split("}")[0]
        self.assertIn("scroll-snap-type: x mandatory", strip)
        # Every snappable child must opt in, separators included.
        self.assertGreaterEqual(self.css.count("scroll-snap-align: start"), 2)

    def test_a_horizontal_fling_stays_inside_the_strip(self):
        # Without this, scrolling past the end triggers the browser back gesture.
        strip = self.css.split(".hourly {")[1].split("}")[0]
        self.assertIn("overscroll-behavior-x: contain", strip)

    def test_each_hour_carries_a_rain_gauge(self):
        self.assertIn('el("div", chance > 0 ? "bar" : "bar idle")', self.js)
        # Wired to the precipitation probability, not just any number.
        self.assertIn('setProperty("--rain", num(hour.precip_prob, 0))', self.js)

    def test_an_empty_track_is_decided_per_hour(self):
        """Deciding it for the whole strip at once meant a single hour at 1%
        made all 48 tracks visible, 47 of them empty grey boxes."""
        self.assertIn("var chance = Number(hour.precip_prob) || 0", self.js)
        self.assertNotIn("anyRain", self.js)
        block = self.css.split(".h .bar.idle {")[1].split("}")[0]
        self.assertIn("background: transparent", block)

    def test_a_dry_forecast_leaves_no_sliver(self):
        block = self.css.split(".h .bar i {")[1].split("}")[0]
        self.assertIn("calc(var(--rain, 0) * 1%)", block)
        self.assertNotIn("min-height", block)

    def test_the_gauge_is_hidden_from_screen_readers(self):
        # The percentage is already announced as text next to it.
        self.assertIn('gauge.setAttribute("aria-hidden", "true")', self.js)


class ExtrasTests(unittest.TestCase):
    """The greeting, the outfit card and the moon row."""

    def setUp(self):
        self.template = read("template.html")
        self.js = read("app.js")
        self.css = read("style.css")

    def test_the_client_wires_all_three(self):
        self.assertIn("function renderGreeting", self.js)
        self.assertIn("function renderOutfit", self.js)
        self.assertIn("function renderSky", self.js)
        for name in ("greeting", "outfit", "sky"):
            self.assertIn('safely("%s"' % name, self.js)

    def test_the_greeting_shouts_once(self):
        # "Καλησπέρα! Κρύο σήμερα." — the exclamation belongs to the greeting
        # word, not to the sentence after it.
        self.assertIn('greeting.word + "!"', self.js)

    def test_each_extra_is_optional(self):
        """All three hide themselves rather than rendering an empty shell."""
        self.assertIn("if (!greeting || !greeting.word) { node.hidden = true; return; }",
                      self.js)
        self.assertIn("if (!outfit) { panel.hidden = true; return; }", self.js)
        self.assertIn("if (!sky) { row.hidden = true; return; }", self.js)

    def test_the_moon_row_is_left_aligned(self):
        # .panel centres its text, which left the phase name floating.
        block = self.css.split(".moon-body {")[1].split("}")[0]
        self.assertIn("text-align: left", block)

    def test_the_stargazing_verdict_carries_its_colour(self):
        self.assertIn('verdict.style.setProperty("--tone"', self.js)

    def test_outfit_items_are_escaped_as_text(self):
        # Built through setText, never innerHTML.
        self.assertIn("setText(chip, item.emoji", self.js)

    def test_the_station_card_reads_the_primary_reading(self):
        """The payload is {primary, all} — one station for the card, the rest
        for the strip at the bottom. Reading it as flat left every field
        undefined, which put "-° τώρα" and "στις undefined" on screen."""
        block = self.js.split("function renderStation(station)")[1]
        block = block.split("\n  function ")[0]
        self.assertIn("station.primary", block)
        # And it must not reach for the flat fields the old shape had.
        self.assertNotIn("station.temp", block)
        self.assertNotIn("station.observed", block)

    def test_the_outfit_card_is_not_centred(self):
        """`.card` sets text-align: center, which is right for a stat tile and
        wrong for a sentence. It cannot be fixed on `.ocard` itself: `.ocard`
        and `.card` are the same element, both are single-class selectors, and
        `.card` is defined later, so it wins on source order."""
        block = self.css.split(".ocard-body {")[1].split("}")[0]
        self.assertIn("text-align: left", block)
        # The ordering trap is real, so assert it rather than assume it.
        self.assertLess(self.css.index(".ocard {"), self.css.index(".card {"))

    def test_the_outfit_text_can_shrink(self):
        """A grid column sizes to max-content by default, so a long headline
        stretched the card past the viewport instead of wrapping."""
        block = self.css.split(".ocard-body {")[1].split("}")[0]
        self.assertIn("minmax(0, 1fr)", block)

    def test_an_empty_chip_row_leaves_no_gap(self):
        self.assertIn(".ocard-items:empty", self.css)


class AccessibilityTests(unittest.TestCase):
    """Preferences the interface has to honour, not merely tolerate."""

    def setUp(self):
        self.css = read("style.css")
        self.js = read("app.js")
        self.template = read("template.html")

    def test_reduced_transparency_drops_the_blur(self):
        block = self.css.split("@media (prefers-reduced-transparency: reduce)")[1]
        block = block.split("@media")[0]
        self.assertIn("backdrop-filter: none", block)
        # And the surfaces become opaque, or the text sits on the weather.
        self.assertIn("background-color: var(--b2)", block)

    def test_increased_contrast_strengthens_the_dim_text(self):
        block = self.css.split("@media (prefers-contrast: more)")[1]
        block = block.split("@media")[0]
        self.assertIn("--text-dim: var(--text)", block)
        self.assertIn("--text-faint: var(--text)", block)

    def test_the_contrast_block_lands_after_the_pointer_block(self):
        """Otherwise the desktop blur rules win on source order and the
        preference is quietly ignored."""
        self.assertLess(self.css.index("@media (hover: hover)"),
                        self.css.index("@media (prefers-contrast: more)"))
        self.assertLess(self.css.index("@media (hover: hover)"),
                        self.css.index("@media (prefers-reduced-transparency"))

    def test_the_chart_describes_its_own_data(self):
        """An aria-label alone announced the topic and none of the numbers."""
        self.assertIn('svg("desc")', self.js)
        self.assertIn("Χωρίς βροχή σε όλο το διάστημα", self.js)
        self.assertIn("Μέγιστη πιθανότητα βροχής", self.js)

    def test_the_chart_keeps_its_role_and_label(self):
        self.assertIn('role="img"', self.template)
        self.assertIn('aria-label', self.template)


class LinkPreviewTests(unittest.TestCase):
    """A shared link should show a card, not a bare URL."""

    def setUp(self):
        self.template = read("template.html")

    def test_the_og_tags_are_present(self):
        for prop in ("og:title", "og:description", "og:image", "og:url",
                     "og:type", "og:locale", "og:image:width", "og:image:height"):
            self.assertIn('property="%s"' % prop, self.template)
        self.assertIn('name="twitter:card"', self.template)

    def test_the_image_is_a_real_file_we_serve(self):
        self.assertIn("/og-image.png", app.STATIC_FILES)
        filename, content_type = app.STATIC_FILES["/og-image.png"]
        self.assertEqual(content_type, "image/png")
        self.assertTrue(os.path.exists(os.path.join(app.BASE_DIR, filename)))

    def test_the_image_is_the_size_the_tags_claim(self):
        with open(os.path.join(app.BASE_DIR, "og-image.png"), "rb") as handle:
            head = handle.read(24)
        self.assertEqual(head[:8], PNG_MAGIC)
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        self.assertEqual((width, height), (1200, 630))

    def test_urls_are_absolute_when_a_public_url_is_configured(self):
        config = sources.Config(public_url="https://example.org")
        rendered = app.ShellCache(app.TEMPLATE).render(config)
        self.assertIn('content="https://example.org/og-image.png"', rendered)

    def test_urls_stay_relative_without_one(self):
        rendered = app.ShellCache(app.TEMPLATE).render(sources.Config())
        self.assertIn('content="/og-image.png"', rendered)


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
            self.assertIn("html.mode-light.theme-" + theme, self.css,
                          "light palette missing for " + theme)

    def test_weather_theme_is_applied_to_the_root_element(self):
        """Both classes must land on <html>, so the canvas colour is always
        right — putting the theme on <body> left <html> flashing a flat
        fallback colour for one frame."""
        self.assertNotIn("body.theme-", self.css)
        self.assertIn("html.theme-clear-day", self.css)

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
        runtime = {"--c", "--level", "--mx", "--my", "--tone", "--rain"}
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
