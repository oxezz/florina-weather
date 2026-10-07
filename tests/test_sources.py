# -*- coding: utf-8 -*-
"""Tests for the service layer: caching, staleness and upstream discipline.

These cover behaviour that is invisible on a working day and only matters when
something upstream is broken, which is exactly when a mistake here costs the
most.
"""

import datetime
import os
import sys
import threading
import time
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import sources  # noqa: E402

UTC = datetime.timezone.utc


def opener_for(counter, failing=()):
    """An opener that counts calls per source and can fail a chosen few."""
    def opener(url, timeout):
        if "archive-api" in url:
            name = "normals"
        elif "air-quality" in url:
            name = "air"
        elif "meteoalarm" in url:
            name = "alerts"
        elif "snowfall_sum" in url:
            name = "snow"
        elif "temperature_2m_mean" in url:
            name = "history"
        elif "models=" in url:
            name = "terrain"
        else:
            name = "forecast"
        counter[name] = counter.get(name, 0) + 1
        if name in failing:
            raise urllib.error.HTTPError(url, 429, "slow down", {}, None)
        return fixtures.dumps({
            "air": fixtures.air,
            "alerts": fixtures.alerts,
            "history": fixtures.daily_history,
            "terrain": fixtures.terrain,
            "forecast": fixtures.forecast,
            "normals": fixtures.normals,
            "snow": fixtures.snow,
        }[name]())
    return opener


def age_entry(service, key, seconds):
    """Backdate a cache entry.

    The cache measures age with ``time.monotonic``, not the injected clock, so
    a test cannot advance time — it has to move the stored stamp instead.
    """
    service.cache._entries[key].stored_at -= seconds


class Clock:
    """A clock the test can move, for the force rate limit."""

    def __init__(self):
        self.now = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)


class FetchingTests(unittest.TestCase):

    def test_every_source_is_fetched(self):
        counter = {}
        service = sources.WeatherService(sources.Config(), opener=opener_for(counter))
        service.snapshot()
        self.assertEqual(sorted(counter), ["air", "alerts", "forecast", "history",
                                           "normals", "snow", "terrain"])

    def test_a_second_snapshot_serves_from_cache(self):
        counter = {}
        service = sources.WeatherService(sources.Config(), opener=opener_for(counter))
        service.snapshot()
        service.snapshot()
        for name, calls in counter.items():
            self.assertEqual(calls, 1, "%s was fetched %d times" % (name, calls))

    def test_concurrent_readers_collapse_into_one_fetch(self):
        """At expiry, a burst of visitors must not all call upstream."""
        counter = {}
        service = sources.WeatherService(sources.Config(), opener=opener_for(counter))

        started = threading.Barrier(6)

        def visit():
            started.wait(timeout=5)
            service.snapshot()

        threads = [threading.Thread(target=visit) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(counter["forecast"], 1)

    def test_a_failing_source_does_not_stop_the_others(self):
        counter = {}
        service = sources.WeatherService(sources.Config(),
                                         opener=opener_for(counter, failing=("air",)))
        snap = service.snapshot()
        self.assertIsNone(snap["air"])
        self.assertIn("air", snap["errors"])
        self.assertIsNotNone(snap["forecast"])
        self.assertIsNotNone(snap["alerts"])

    def test_a_lagging_source_does_not_hold_up_the_page(self):
        """Open-Meteo is usually under a second but occasionally takes twenty,
        and the forecast is ready long before that."""
        counter = {}
        healthy = opener_for(counter)

        def lagging(url, timeout):
            if "air-quality" in url:
                time.sleep(2.0)
            return healthy(url, timeout)

        config = sources.Config(snapshot_deadline=0.5)
        service = sources.WeatherService(config, opener=lagging)
        started = time.time()
        snap = service.snapshot()

        self.assertLess(time.time() - started, 1.5)
        self.assertIsNotNone(snap["forecast"])
        self.assertIsNone(snap["air"])
        self.assertIn("air", snap["errors"])


class NegativeCacheTests(unittest.TestCase):

    def test_a_failed_source_is_left_alone_for_a_while(self):
        """Otherwise every single page view retries a source that is down."""
        counter = {}
        service = sources.WeatherService(sources.Config(),
                                         opener=opener_for(counter, failing=("air",)))
        service.snapshot()
        after_first = counter["air"]           # the built-in retries
        self.assertGreaterEqual(after_first, 1)

        for _ in range(5):
            service.snapshot()
        self.assertEqual(counter["air"], after_first,
                         "a failing source was retried on later page views")

    def test_the_source_is_tried_again_once_the_window_passes(self):
        counter = {}
        clock = Clock()
        config = sources.Config(failure_ttl=45.0)
        service = sources.WeatherService(config, opener=opener_for(counter, failing=("air",)),
                                         clock=clock)
        service.snapshot()
        after_first = counter["air"]
        clock.advance(120)
        service.snapshot()
        self.assertGreater(counter["air"], after_first)


class ForceTests(unittest.TestCase):
    """``?force`` must re-read upstream without throwing away the fallback."""

    def setUp(self):
        self.counter = {}
        self.clock = Clock()
        self.service = sources.WeatherService(
            sources.Config(), opener=opener_for(self.counter), clock=self.clock)
        self.service.snapshot()

    def test_force_re_reads_the_forecast(self):
        age_entry(self.service, "forecast", 10_000)
        self.service.snapshot(force=True)
        self.assertEqual(self.counter["forecast"], 2)

    def test_force_keeps_the_entry_it_could_not_replace(self):
        """The old code dropped the entry first, so a failed refresh left the
        page with nothing to fall back on."""
        age_entry(self.service, "forecast", 10_000)
        self.service.opener = opener_for(self.counter, failing=("forecast",))
        self.service._opener = self.service.opener

        snap = self.service.snapshot(force=True)
        self.assertIsNotNone(snap["forecast"], "force discarded the stale entry")
        self.assertTrue(snap["stale"])

    def test_force_is_rate_limited(self):
        age_entry(self.service, "forecast", 10_000)
        self.service.snapshot(force=True)
        after_first = self.counter["forecast"]

        # The forced call above refreshed the entry, so from here a normal
        # read is a cache hit. Only a *second* force would reach upstream,
        # and that is what the interval must refuse.
        for _ in range(5):
            self.service.snapshot(force=True)
        self.assertEqual(self.counter["forecast"], after_first,
                         "held-down refresh hammered upstream")

    def test_force_works_again_after_the_interval(self):
        age_entry(self.service, "forecast", 10_000)
        self.service.snapshot(force=True)
        after_first = self.counter["forecast"]

        age_entry(self.service, "forecast", 10_000)
        self.clock.advance(120)
        self.service.snapshot(force=True)
        self.assertEqual(self.counter["forecast"], after_first + 1)


class StaleTests(unittest.TestCase):

    def test_stale_data_is_served_when_upstream_fails(self):
        counter = {}
        service = sources.WeatherService(sources.Config(), opener=opener_for(counter))
        service.snapshot()

        age_entry(service, "forecast", 10_000)      # past the TTL, inside max_stale
        service._opener = opener_for(counter, failing=("forecast",))
        snap = service.snapshot()

        self.assertIsNotNone(snap["forecast"])
        self.assertTrue(snap["stale"])
        self.assertIn("forecast", snap["errors"])

    def test_data_beyond_max_stale_is_not_served(self):
        counter = {}
        config = sources.Config(max_stale=3600.0)
        service = sources.WeatherService(config, opener=opener_for(counter))
        service.snapshot()

        age_entry(service, "forecast", 10_000)      # older than max_stale
        service._opener = opener_for(counter, failing=("forecast",))
        snap = service.snapshot()

        self.assertIsNone(snap["forecast"])
        self.assertIn("forecast", snap["errors"])


if __name__ == "__main__":
    unittest.main()
