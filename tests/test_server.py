# -*- coding: utf-8 -*-
"""End-to-end tests for the HTTP layer.

A real server is started on an ephemeral port with an injected offline opener,
so the whole request path is exercised without touching the network.
"""

import datetime
import gzip
import http.client
import json
import os
import ssl
import sys
import threading
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import app  # noqa: E402
import fixtures  # noqa: E402
import sources  # noqa: E402


def recording_opener(counter):
    """A stand-in for the network that answers from the fixtures."""
    def opener(url, timeout):
        if "archive-api" in url:
            counter["normals"] = counter.get("normals", 0) + 1
            return fixtures.dumps(fixtures.normals())
        if "air-quality" in url:
            counter["air"] = counter.get("air", 0) + 1
            return fixtures.dumps(fixtures.air())
        if "meteoalarm" in url:
            counter["alerts"] = counter.get("alerts", 0) + 1
            # Anchored to the real clock: this opener feeds the live HTTP
            # server, so a fixed warning window silently expires one day and
            # the assertion starts failing on a date rather than on a bug.
            return fixtures.dumps(
                fixtures.alerts(now=datetime.datetime.now(datetime.timezone.utc)))
        if "snowfall_sum" in url:
            counter["snow"] = counter.get("snow", 0) + 1
            return fixtures.dumps(fixtures.snow())
        # Same host as the forecast, but daily-only: the HDD history call.
        if "temperature_2m_mean" in url:
            counter["history"] = counter.get("history", 0) + 1
            return fixtures.dumps(fixtures.daily_history())
        # Two locations and several models: the terrain / inversion call.
        if "models=" in url:
            counter["terrain"] = counter.get("terrain", 0) + 1
            return fixtures.dumps(fixtures.terrain())
        counter["forecast"] = counter.get("forecast", 0) + 1
        return fixtures.dumps(fixtures.forecast())
    return opener


def failing_opener(url, timeout):
    raise urllib.error.HTTPError(url, 400, "bad request", {}, None)


class RunningServer:
    """Context manager: a real HTTP server on 127.0.0.1:<ephemeral>."""

    def __init__(self, opener, **config_kwargs):
        self.config = sources.Config(host="127.0.0.1", port=0, **config_kwargs)
        self.service = sources.WeatherService(self.config, opener=opener)
        self.httpd = app.create_server(self.config, service=self.service)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False

    def request(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def json(self, path, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        return status, headers, json.loads(body.decode("utf-8"))


class ShellTests(unittest.TestCase):

    def test_index_renders_the_shell(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/")
            text = body.decode("utf-8")
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            self.assertIn("Φλώρινα", text)
            self.assertIn("Δυτική Μακεδονία", text)
            self.assertIn('data-refresh="', text)
            self.assertNotIn("{{", text, "unsubstituted placeholder left in the page")

    def test_security_headers(self):
        with RunningServer(recording_opener({})) as server:
            _status, headers, _body = server.request("/")
            self.assertIn("Content-Security-Policy", headers)
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_index_html_alias(self):
        with RunningServer(recording_opener({})) as server:
            status, _headers, _body = server.request("/index.html")
            self.assertEqual(status, 200)

    def test_head_has_no_body_but_reports_length(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/", method="HEAD")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")
            self.assertGreater(int(headers["Content-Length"]), 0)


class ApiTests(unittest.TestCase):

    def test_weather_payload(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, data = server.json("/api/weather",
                                                headers={"Accept": "application/json"})
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
            self.assertEqual(data["place"], "Φλώρινα")
            self.assertEqual(data["current"]["text"], "Καθαρός")
            self.assertEqual(len(data["hourly"]), 48)
            self.assertEqual(len(data["daily"]), 7)
            self.assertEqual(len(data["alerts"]), 1)
            self.assertIn("summary", data)

    def test_forecast_is_cached_between_requests(self):
        counter = {}
        with RunningServer(recording_opener(counter)) as server:
            for _ in range(3):
                status, _headers, _data = server.json(
                    "/api/weather", headers={"Accept": "application/json"})
                self.assertEqual(status, 200)
            self.assertEqual(counter["forecast"], 1)
            self.assertEqual(counter["air"], 1)
            self.assertEqual(counter["alerts"], 1)

    def test_force_bypasses_the_cache(self):
        counter = {}
        with RunningServer(recording_opener(counter)) as server:
            server.json("/api/weather", headers={"Accept": "application/json"})
            server.json("/api/weather?force", headers={"Accept": "application/json"})
            self.assertEqual(counter["forecast"], 2)
            # The slower sources keep their own TTL.
            self.assertEqual(counter["alerts"], 1)

    def test_weather_is_never_cached_by_the_browser(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, _body = server.request(
                "/api/weather", headers={"Accept": "application/json"})
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")

    def test_gzip_is_negotiated(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request(
                "/api/weather", headers={"Accept": "application/json",
                                         "Accept-Encoding": "gzip"})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Content-Encoding"), "gzip")
            data = json.loads(gzip.decompress(body).decode("utf-8"))
            self.assertEqual(data["current"]["text"], "Καθαρός")

    def test_no_gzip_without_the_header(self):
        with RunningServer(recording_opener({})) as server:
            _status, headers, _body = server.request(
                "/api/weather", headers={"Accept": "application/json"})
            self.assertNotIn("Content-Encoding", headers)

    def test_health(self):
        with RunningServer(recording_opener({})) as server:
            status, _headers, data = server.json("/api/health")
            self.assertEqual(status, 200)
            self.assertIn("ok", data)

    def test_upstream_failure_is_a_json_503_for_the_client(self):
        with RunningServer(failing_opener) as server:
            status, headers, data = server.json(
                "/api/weather", headers={"Accept": "application/json"})
            self.assertEqual(status, 503)
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
            self.assertIn("error", data)

    def test_upstream_failure_is_html_when_navigating(self):
        with RunningServer(failing_opener) as server:
            status, headers, body = server.request(
                "/api/weather", headers={"Accept": "text/html"})
            self.assertEqual(status, 503)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            self.assertIn("καιρού", body.decode("utf-8"))

    def test_unknown_api_path_is_json_404(self):
        with RunningServer(recording_opener({})) as server:
            status, _headers, data = server.json(
                "/api/nope", headers={"Accept": "application/json"})
            self.assertEqual(status, 404)
            self.assertIn("error", data)


class StaticTests(unittest.TestCase):

    def test_stylesheet(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/style.css")
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith("text/css"))
            self.assertIn(b"--glass-bg", body)

    def test_javascript(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/app.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", headers["Content-Type"])
            self.assertIn(b"/api/weather", body)

    def test_theme_bootstrap_is_served(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/theme.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", headers["Content-Type"])
            self.assertIn(b"prefers-color-scheme", body)

    def test_service_worker_is_served_and_never_cached(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/sw.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", headers["Content-Type"])
            # A cached worker means a browser keeps running the old version.
            self.assertEqual(headers["Cache-Control"], "no-cache")
            self.assertIn(b"addEventListener", body)

    def test_manifest_is_served_with_the_right_type(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/manifest.webmanifest")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-cache")
            data = json.loads(body.decode("utf-8"))
            self.assertEqual(data["start_url"], "/")

    def test_pwa_icons_are_served(self):
        with RunningServer(recording_opener({})) as server:
            for path in ("/icon-180.png", "/icon-192.png", "/icon-512.png",
                         "/icon-maskable-512.png", "/icon-square.svg"):
                status, headers, body = server.request(path)
                self.assertEqual(status, 200, path)
                self.assertTrue(headers["Content-Type"].startswith("image/"), path)
                self.assertGreater(len(body), 100, path)

    def test_favicon(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, _body = server.request("/favicon.svg")
            self.assertEqual(status, 200)
            self.assertIn("svg", headers["Content-Type"])

    def test_static_etag(self):
        with RunningServer(recording_opener({})) as server:
            _status, headers, _body = server.request("/style.css")
            status, _headers, body = server.request(
                "/style.css", headers={"If-None-Match": headers["ETag"]})
            self.assertEqual(status, 304)
            self.assertEqual(body, b"")

    def test_source_files_are_not_served(self):
        with RunningServer(recording_opener({})) as server:
            for path in ("/app.py", "/greek.py", "/..%2fapp.py", "/template.html"):
                status, _headers, _body = server.request(path)
                self.assertEqual(status, 404, "%s should not be served" % path)

    def test_unknown_path_is_404(self):
        with RunningServer(recording_opener({})) as server:
            status, headers, body = server.request("/does-not-exist")
            self.assertEqual(status, 404)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            self.assertIn("δεν βρέθηκε", body.decode("utf-8"))

    def test_post_is_rejected(self):
        with RunningServer(recording_opener({})) as server:
            status, _headers, _body = server.request("/", method="POST")
            self.assertEqual(status, 405)


class ConfigTests(unittest.TestCase):

    def test_env_defaults_are_sane(self):
        config = sources.Config()
        self.assertEqual(config.place, "Φλώρινα")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.forecast_hours, 48)
        self.assertEqual(config.forecast_days, 7)
        self.assertIn("west macedonia", config.alert_areas)

    def test_refresh_is_not_faster_than_the_models_update(self):
        # Open-Meteo refreshes roughly every 15 minutes; polling far more often
        # than that just burns the user's bandwidth.
        config = sources.Config()
        self.assertGreaterEqual(config.refresh, 300)
        self.assertGreaterEqual(config.cache_ttl, config.refresh / 2)

    def test_env_overrides(self):
        os.environ["FLORINA_PLACE"] = "Testville"
        os.environ["FLORINA_PORT"] = "9123"
        os.environ["FLORINA_ALERT_AREAS"] = "a, b"
        try:
            config = sources.Config.from_env()
            self.assertEqual(config.place, "Testville")
            self.assertEqual(config.port, 9123)
            self.assertEqual(config.alert_areas, ["a", "b"])
        finally:
            for key in ("FLORINA_PLACE", "FLORINA_PORT", "FLORINA_ALERT_AREAS"):
                os.environ.pop(key, None)

    def test_cli_overrides_win_over_env(self):
        os.environ["FLORINA_PORT"] = "9123"
        try:
            config = sources.Config.from_env(port=7777)
            self.assertEqual(config.port, 7777)
        finally:
            os.environ.pop("FLORINA_PORT", None)

    def test_generic_port_env_is_honoured_for_paas_hosts(self):
        os.environ["PORT"] = "10000"
        try:
            self.assertEqual(sources.Config.from_env().port, 10000)
        finally:
            os.environ.pop("PORT", None)

    def test_florina_port_beats_the_generic_port(self):
        os.environ["PORT"] = "10000"
        os.environ["FLORINA_PORT"] = "9000"
        try:
            self.assertEqual(sources.Config.from_env().port, 9000)
        finally:
            for key in ("PORT", "FLORINA_PORT"):
                os.environ.pop(key, None)


class TlsTests(unittest.TestCase):
    """A host with no CA bundle must still be able to verify certificates.

    Wasmer Edge's Python loads zero certificates by default, which made every
    upstream call fail with CERTIFICATE_VERIFY_FAILED.
    """

    def setUp(self):
        self._saved = (sources._context, sources._context_source)
        sources._context = None
        sources._context_source = None
        self._env = {name: os.environ.pop(name, None) for name in sources.CA_ENV_VARS}

    def tearDown(self):
        sources._context, sources._context_source = self._saved
        for name, value in self._env.items():
            if value is not None:
                os.environ[name] = value

    def test_a_bundle_is_shipped_with_the_app(self):
        self.assertTrue(os.path.isfile(sources.BUNDLED_CA),
                        "cacert.pem must ship with the app")

    def test_the_bundled_file_is_a_valid_ca_bundle(self):
        context = ssl.create_default_context(cafile=sources.BUNDLED_CA)
        self.assertGreater(context.cert_store_stats().get("x509", 0), 100,
                           "the shipped bundle should hold the full Mozilla list")

    def test_the_context_actually_loads_certificates(self):
        context = sources.ssl_context()
        self.assertGreater(context.cert_store_stats().get("x509", 0), 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_the_bundle_is_used_when_the_system_store_is_empty(self):
        """Model a host like Wasmer Edge, whose default context loads nothing."""
        original = ssl.create_default_context

        def create_default(*args, **kwargs):
            if kwargs.get("cafile"):
                return original(*args, **kwargs)
            # A fresh client context with no CA certificates loaded at all.
            empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            empty.verify_mode = ssl.CERT_REQUIRED
            empty.check_hostname = True
            return empty

        sources.ssl.create_default_context = create_default
        try:
            self.assertEqual(create_default().cert_store_stats().get("x509", 0), 0,
                             "the fake empty store must really be empty")
            sources._context = None
            context = sources.ssl_context()
            self.assertGreater(context.cert_store_stats().get("x509", 0), 0)
            self.assertEqual(sources.tls_source(), "bundled cacert.pem")
        finally:
            sources.ssl.create_default_context = original

    def test_an_explicit_env_override_wins(self):
        os.environ["SSL_CERT_FILE"] = sources.BUNDLED_CA
        try:
            sources._context = None
            sources.ssl_context()
            self.assertTrue(sources.tls_source().startswith("SSL_CERT_FILE="))
        finally:
            os.environ.pop("SSL_CERT_FILE", None)

    def test_a_bogus_env_override_is_ignored(self):
        os.environ["SSL_CERT_FILE"] = os.path.join("nowhere", "missing.pem")
        try:
            sources._context = None
            context = sources.ssl_context()
            self.assertGreater(context.cert_store_stats().get("x509", 0), 0)
            self.assertNotIn("nowhere", sources.tls_source())
        finally:
            os.environ.pop("SSL_CERT_FILE", None)

    def test_health_reports_the_trust_store(self):
        with RunningServer(recording_opener({})) as server:
            status, _headers, data = server.json("/api/health")
            self.assertEqual(status, 200)
            self.assertIn("tls", data)

    def test_cli_parser(self):
        args = app.parse_args(["--port", "9001", "--place", "X", "--verbose"])
        self.assertEqual(args.port, 9001)
        self.assertEqual(args.place, "X")
        self.assertTrue(args.verbose)
        self.assertFalse(args.open)

    def test_cli_alert_and_hour_flags(self):
        args = app.parse_args(["--alert-areas", "a, b", "--alert-emma-ids", "GR009",
                               "--forecast-hours", "24"])
        config = sources.Config.from_env(
            alert_areas=app._split(args.alert_areas),
            alert_emma_ids=app._split(args.alert_emma_ids),
            forecast_hours=args.forecast_hours,
        )
        self.assertEqual(config.alert_areas, ["a", "b"])
        self.assertEqual(config.alert_emma_ids, ["GR009"])
        self.assertEqual(config.forecast_hours, 24)

    def test_cli_omitted_alert_flags_leave_env_defaults(self):
        args = app.parse_args([])
        config = sources.Config.from_env(alert_areas=app._split(args.alert_areas))
        self.assertIn("west macedonia", config.alert_areas)


if __name__ == "__main__":
    unittest.main(verbosity=2)
