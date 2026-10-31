# -*- coding: utf-8 -*-
"""Tests for the outbound frost alerts.

The important properties are negative ones: nothing is sent when the transport
is unconfigured or the frost is only a maybe, the same alert is not repeated,
and a dead transport never reaches the page.
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import notify  # noqa: E402
import sources  # noqa: E402

NOW = datetime.datetime(2026, 10, 7, 6, 0, tzinfo=datetime.timezone.utc)


def frost(level="frost", count=2, ground=-4.2, air=0.4):
    return {
        "level": level, "label": "Παγετός", "count": count,
        "ground_min": ground, "air_min": air, "min": ground,
        "first": {"label": "Αύριο", "iso": "2026-10-08"},
    }


def report_with(frost_card, place="Φλώρινα"):
    return {"place": place, "local": {"frost": frost_card} if frost_card else {}}


class Recorder:
    """A stand-in for urlopen that records requests instead of sending them."""

    def __init__(self, status=200, error=None):
        self.calls = []
        self.status = status
        self.error = error

    def __call__(self, request, timeout):
        self.calls.append({
            "url": request.full_url,
            "body": (request.data or b"").decode("utf-8", "replace"),
            # urllib normalises header names ("Content-type"), so lower-case
            # them here rather than have every assertion remember that.
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "method": request.get_method(),
        })
        if self.error:
            raise self.error
        outer = self

        class Response:
            status = outer.status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response()


class MessageTests(unittest.TestCase):

    def test_a_maybe_stays_silent(self):
        """'risk' means a night near freezing, not a freeze. Not worth a buzz."""
        self.assertIsNone(notify.frost_message(frost("risk"), "Φλώρινα"))

    def test_an_actual_freeze_speaks_up(self):
        for level in ("frost", "hard", "severe"):
            self.assertIsNotNone(notify.frost_message(frost(level), "Φλώρινα"),
                                 "%s should alert" % level)

    def test_the_message_names_the_place_and_both_sensors(self):
        title, body = notify.frost_message(frost(), "Φλώρινα")
        self.assertIn("Φλώρινα", title)
        self.assertIn("Παγετός", title)
        self.assertIn("2 νύχτες", body)
        self.assertIn("Αύριο", body)
        self.assertIn("-4.2", body)
        self.assertIn("+0.4", body)

    def test_a_single_night_is_not_pluralised(self):
        _title, body = notify.frost_message(frost(count=1), "Φλώρινα")
        self.assertIn("1 νύχτα", body)
        self.assertNotIn("νύχτες", body)

    def test_missing_data_does_not_break_the_message(self):
        sparse = {"level": "frost", "label": "Παγετός"}
        title, body = notify.frost_message(sparse, "Φλώρινα")
        self.assertIn("Παγετός", title)
        self.assertIsInstance(body, str)


class ConfigurationTests(unittest.TestCase):

    def test_nothing_is_configured_by_default(self):
        self.assertIsNone(notify.configured(sources.Config()))

    def test_ntfy_needs_a_url(self):
        self.assertIsNone(notify.configured(
            sources.Config(alert_webhook="ntfy")))
        self.assertEqual(notify.configured(
            sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh/x")),
            "ntfy")

    def test_telegram_needs_both_a_token_and_a_chat(self):
        self.assertIsNone(notify.configured(
            sources.Config(alert_webhook="telegram", telegram_token="t")))
        self.assertIsNone(notify.configured(
            sources.Config(alert_webhook="telegram", telegram_chat="c")))
        self.assertEqual(notify.configured(sources.Config(
            alert_webhook="telegram", telegram_token="t", telegram_chat="c")),
            "telegram")

    def test_an_unknown_transport_is_ignored(self):
        self.assertIsNone(notify.configured(
            sources.Config(alert_webhook="carrier-pigeon", ntfy_url="x")))


class SendingTests(unittest.TestCase):

    def setUp(self):
        notify._last_sent.clear()

    def test_ntfy_publishes_through_the_json_body(self):
        """Header values are latin-1, so a Greek title sent as a header arrives
        mangled; the JSON form keeps everything in the body."""
        recorder = Recorder()
        config = sources.Config(alert_webhook="ntfy",
                                ntfy_url="https://ntfy.sh/florina-frost")
        result = notify.notify_frost(config, report_with(frost()), NOW, recorder)
        self.assertTrue(result["sent"])
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://ntfy.sh/")
        self.assertIn("application/json", call["headers"].get("content-type", ""))

        payload = json.loads(call["body"])
        self.assertEqual(payload["topic"], "florina-frost")
        self.assertIn("Παγετός", payload["title"])
        self.assertIn("Αύριο", payload["message"])

    def test_ntfy_keeps_an_instance_prefix(self):
        recorder = Recorder()
        config = sources.Config(alert_webhook="ntfy",
                                ntfy_url="https://ntfy.example.com/ntfy/mytopic")
        notify.notify_frost(config, report_with(frost()), NOW, recorder)
        call = recorder.calls[0]
        self.assertEqual(call["url"], "https://ntfy.example.com/ntfy/")
        self.assertEqual(json.loads(call["body"])["topic"], "mytopic")

    def test_an_ntfy_url_without_a_topic_fails_softly(self):
        recorder = Recorder()
        config = sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh")
        result = notify.notify_frost(config, report_with(frost()), NOW, recorder)
        self.assertFalse(result["sent"])
        self.assertEqual(recorder.calls, [])

    def test_telegram_posts_to_the_bot_api(self):
        recorder = Recorder()
        config = sources.Config(alert_webhook="telegram",
                                telegram_token="123:abc", telegram_chat="42")
        result = notify.notify_frost(config, report_with(frost()), NOW, recorder)
        self.assertTrue(result["sent"])
        call = recorder.calls[0]
        self.assertIn("api.telegram.org/bot123:abc/sendMessage", call["url"])
        self.assertIn("chat_id=42", call["body"])

    def test_nothing_is_sent_when_unconfigured(self):
        recorder = Recorder()
        result = notify.notify_frost(sources.Config(), report_with(frost()),
                                     NOW, recorder)
        self.assertFalse(result["sent"])
        self.assertEqual(recorder.calls, [])

    def test_nothing_is_sent_without_a_frost_card(self):
        recorder = Recorder()
        config = sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh/x")
        result = notify.notify_frost(config, report_with(None), NOW, recorder)
        self.assertFalse(result["sent"])
        self.assertEqual(recorder.calls, [])

    def test_a_dead_transport_does_not_raise(self):
        """An alert is not worth a 500 on the weather page."""
        recorder = Recorder(error=urllib.error.URLError("no route to host"))
        config = sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh/x")
        result = notify.notify_frost(config, report_with(frost()), NOW, recorder)
        self.assertFalse(result["sent"])
        self.assertIn("no route", result["reason"])


class SuppressionTests(unittest.TestCase):

    def setUp(self):
        notify._last_sent.clear()
        self.config = sources.Config(alert_webhook="ntfy",
                                     ntfy_url="https://ntfy.sh/x")

    def test_the_same_alert_is_not_sent_twice(self):
        """The frost card stays lit for days; without this the warning would
        go out on every single request."""
        first = Recorder()
        notify.notify_frost(self.config, report_with(frost()), NOW, first)
        self.assertEqual(len(first.calls), 1)

        second = Recorder()
        result = notify.notify_frost(self.config, report_with(frost()), NOW, second)
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "already sent")
        self.assertEqual(second.calls, [])

    def test_it_sends_again_once_the_window_passes(self):
        notify.notify_frost(self.config, report_with(frost()), NOW, Recorder())
        later = NOW + datetime.timedelta(hours=12)
        recorder = Recorder()
        result = notify.notify_frost(self.config, report_with(frost()), later,
                                     recorder)
        self.assertTrue(result["sent"])
        self.assertEqual(len(recorder.calls), 1)

    def test_a_worse_alert_is_not_suppressed(self):
        notify.notify_frost(self.config, report_with(frost("frost")), NOW,
                            Recorder())
        recorder = Recorder()
        result = notify.notify_frost(self.config, report_with(frost("severe")),
                                     NOW, recorder)
        self.assertTrue(result["sent"], "an escalation must get through")

    def test_a_zero_window_disables_suppression(self):
        config = sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh/x",
                                webhook_min_interval=0)
        notify.notify_frost(config, report_with(frost()), NOW, Recorder())
        recorder = Recorder()
        self.assertTrue(notify.notify_frost(config, report_with(frost()), NOW,
                                            recorder)["sent"])

    def test_state_survives_a_restart_through_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "state.json")
            config = sources.Config(alert_webhook="ntfy",
                                    ntfy_url="https://ntfy.sh/x",
                                    webhook_state_path=path)
            notify.notify_frost(config, report_with(frost()), NOW, Recorder())
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                self.assertTrue(json.load(handle))

            notify._last_sent.clear()          # pretend the process restarted
            recorder = Recorder()
            result = notify.notify_frost(config, report_with(frost()), NOW, recorder)
            self.assertFalse(result["sent"])
            self.assertEqual(recorder.calls, [])

    def test_a_read_only_state_path_is_survivable(self):
        config = sources.Config(alert_webhook="ntfy", ntfy_url="https://ntfy.sh/x",
                                webhook_state_path="/definitely/not/writable.json")
        result = notify.notify_frost(config, report_with(frost()), NOW, Recorder())
        self.assertTrue(result["sent"])
        # And the in-memory record still suppresses the repeat.
        second = Recorder()
        self.assertFalse(notify.notify_frost(config, report_with(frost()), NOW,
                                             second)["sent"])


if __name__ == "__main__":
    unittest.main()
