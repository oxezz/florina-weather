#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Καιρός · Φλώρινα — a small self-hosted weather page.

Serves a single-page UI plus a JSON API for one town, using only the Python
standard library. Run it with::

    python app.py

then open http://127.0.0.1:8000.

Nothing here talks to the network directly: :mod:`sources` does the fetching
and caching, :mod:`report` shapes the data, this module only does HTTP.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import logging
import os
import re
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import greek
import geography
import notify
import radar
import report as report_mod
import sources

__version__ = "3.15.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE_DIR, "template.html")

# Only these files are ever served from disk, and only under these URLs.
STATIC_FILES = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/radar.js": ("radar.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/icon-square.svg": ("icon-square.svg", "image/svg+xml"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    "/og-image.png": ("og-image.png", "image/png"),
}

# The service worker and the manifest must never be served stale, or a browser
# keeps running the previous version.
UNCACHED_STATIC = ("/sw.js", "/manifest.webmanifest")

MIN_GZIP_BYTES = 512

log = logging.getLogger("florina.server")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _gzip_ok(accept_encoding):
    return "gzip" in (accept_encoding or "").lower()


def _etag(body):
    return '"%s"' % hashlib.sha1(body).hexdigest()[:20]


# --------------------------------------------------------------------------
# Shrinking what goes on the wire
#
# The source files keep their comments; only the bytes a browser receives lose
# them. Together that is 29.1 KB of gzipped assets down to 19.8 KB - 32 %, and
# the biggest single win available without a build step, because comments and
# indentation are exactly what gzip is worst at.
# --------------------------------------------------------------------------


def _css_tokens(text):
    """The significant token sequence of a stylesheet, comments removed."""
    text = _CSS_STRING_OR_COMMENT.sub(
        lambda m: "" if m.group(0).startswith("/*") else m.group(0), text)
    return re.findall(r"[{};:]|[^;{}:\s]+", text)


# A quoted string, or a comment. Matching them in one alternation is the point:
# the scanner steps over a string whole, so a "/*" inside quotes can never be
# mistaken for the start of a comment.
_CSS_STRING_OR_COMMENT = re.compile(
    r'"(?:[^"\\]|\\.)*"'      # "..."
    r"|'(?:[^'\\]|\\.)*'"     # '...'
    r"|/\*.*?\*/",            # /* ... */
    re.S)


def _strip_css(text):
    """Drop comments and indentation, leaving quoted strings intact.

    Removing every ``/*...*/`` outright is the obvious version and it is wrong:
    ``content: "/*"`` would lose most of its value. Walking strings and
    comments together avoids the question rather than checking for it
    afterwards.
    """
    text = _CSS_STRING_OR_COMMENT.sub(
        lambda match: "" if match.group(0).startswith("/*") else match.group(0),
        text)
    text = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", text)
    return re.sub(r"\n{2,}", "\n", text)


def _strip_js(text):
    """Drop whole-line comments and indentation; trailing comments stay.

    Deliberately conservative, with no tokeniser, so a "//" inside a string or
    a regex can never be mistaken for a comment. Every rule here is anchored to
    the start of a line and removes only whitespace or a whole-line comment,
    which cannot change meaning: JavaScript's automatic semicolon insertion
    depends on newlines, and these stay.
    """
    text = re.sub(r"(?ms)^[ \t]*/\*.*?\*/[ \t]*\n", "", text)
    text = re.sub(r"(?m)^[ \t]*//.*\n", "", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    return re.sub(r"\n{2,}", "\n", text)


# What gets shrunk before it is cached and sent.
_STRIPPERS = {"/style.css": _strip_css, "/app.js": _strip_js,
              "/theme.js": _strip_js, "/radar.js": _strip_js}

# Served under a ?v=<release> URL, so the bytes behind any given URL are fixed.
_VERSIONED = ("/style.css", "/app.js", "/theme.js", "/radar.js")


def _cache_control(path, query):
    """How long a static file may be kept, and whether it may be revalidated.

    The page asks for ``/app.js?v=<release>``, and those bytes cannot change
    under that URL, so it may be kept for a year. Everything else - the worker,
    the manifest, the icons, an unversioned request - revalidates as before.
    """
    if path in _VERSIONED and query.get("v") == [__version__]:
        return "public, max-age=31536000, immutable"
    if path in UNCACHED_STATIC:
        return "no-cache"
    return "public, max-age=300"


class StaticCache:
    """Reads, shrinks and gzips each static file once, then only when it changes.

    Without this every request re-read the file from disk, re-hashed it for the
    ETag and re-compressed it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}

    def get(self, path):
        """``(etag, body, gzipped_or_None)``; raises OSError if the file is gone."""
        filename, content_type = STATIC_FILES[path]
        full = os.path.join(BASE_DIR, filename)
        stamp = os.stat(full).st_mtime_ns
        with self._lock:
            item = self._items.get(path)
            if item and item[0] == stamp:
                return item[1]
        with open(full, "rb") as handle:
            body = handle.read()
        if path == "/sw.js":
            # The worker's cache name and shell URLs follow the release, so a
            # deploy can no longer forget to bump a constant by hand.
            body = body.replace(b"{{VERSION}}", __version__.encode())
        elif path in _STRIPPERS:
            body = _STRIPPERS[path](body.decode("utf-8")).encode("utf-8")
        gz = None
        if len(body) >= MIN_GZIP_BYTES and not content_type.startswith("image/"):
            gz = gzip.compress(body, 9)
        entry = (_etag(body), body, gz)
        with self._lock:
            self._items[path] = (stamp, entry)
        return entry


class ShellCache:
    """Reads ``template.html`` and remembers it until the file changes."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._text = None

    def read(self):
        with self._lock:
            try:
                stamp = os.stat(self.path).st_mtime
            except OSError as exc:
                raise RuntimeError("cannot read %s: %s" % (self.path, exc)) from exc
            if self._text is None or stamp != self._mtime:
                with open(self.path, encoding="utf-8") as handle:
                    self._text = handle.read()
                self._mtime = stamp
            return self._text

    def render(self, config):
        """Fill the handful of placeholders the shell needs."""
        text = self.read()
        values = {
            "TITLE": config.title(),
            "PLACE": config.place,
            "REGION": config.region,
            "REFRESH": str(config.refresh),
            "REFRESH_TEXT": greek.format_interval(config.refresh),
            "VERSION": __version__,
            # The inversion hint names the place and the comparison point, so
            # both have to follow the configuration rather than be typed in.
            "SLOPE_NAME": config.slope_name,
            # Open Graph. Scrapers want absolute URLs, so they are built from
            # public_url when it is set and left as root-relative paths when it
            # is not - which most scrapers resolve against the page URL anyway.
            "OG_URL": (config.public_url or "").rstrip("/") + "/",
            "OG_IMAGE": (config.public_url or "").rstrip("/") + "/og-image.png",
            "DESCRIPTION": (
                "Τρέχουσες συνθήκες, 48ωρη πρόγνωση, 7ήμερος, ποιότητα αέρα "
                "και τοπικό μικροκλίμα — %s, %s." % (config.place, config.region)),
        }
        for key, value in values.items():
            text = text.replace("{{%s}}" % key, html.escape(str(value)))
        return text


def render_error_page(status, message, hint=None):
    """A friendly, self-contained Greek error page."""
    safe_title = html.escape("%s %s" % (status, HTTPStatus(status).phrase))
    body = [
        "<!DOCTYPE html>",
        '<html lang="el"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % safe_title,
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",
        "background:#0c2339;color:#f4f8ff;display:flex;align-items:center;",
        "justify-content:center;min-height:100vh;margin:0;padding:24px}",
        ".box{max-width:520px;background:rgba(255,255,255,.10);",
        "border:1px solid rgba(255,255,255,.18);border-radius:24px;padding:32px;text-align:center}",
        "h1{font-size:1.4rem;margin:0 0 12px}p{opacity:.85;line-height:1.5;margin:0 0 10px}",
        "code{background:rgba(0,0,0,.3);padding:2px 8px;border-radius:8px;font-size:.85rem}",
        "a{color:#9fd8ff}",
        "</style></head><body><div class='box'>",
        "<h1>%s</h1>" % safe_title,
        "<p>%s</p>" % html.escape(message),
    ]
    if hint:
        body.append("<p>%s</p>" % html.escape(hint))
    body.append("<p><a href='/'>Δοκιμάστε ξανά</a></p>")
    body.append("</div></body></html>")
    return "\n".join(body).encode("utf-8")


# --------------------------------------------------------------------------
# Request handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "FlorinaWeather/" + __version__
    protocol_version = "HTTP/1.1"  # keep-alive; requires exact Content-Length

    # populated by create_server()
    service: sources.WeatherService = None
    shell: ShellCache = None
    statics = None
    config: sources.Config = None
    radar = None

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        log.info("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt, *args):  # noqa: A003 - stdlib signature
        log.warning("%s %s", self.address_string(), fmt % args)

    def _send(self, status, body, content_type, extra_headers=None, compress=True):
        if isinstance(body, str):
            body = body.encode("utf-8")
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        if extra_headers:
            headers.update(extra_headers)

        if compress and len(body) >= MIN_GZIP_BYTES and _gzip_ok(
                self.headers.get("Accept-Encoding")):
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # No ETag here on purpose: the payload embeds a "generated at" stamp and
        # a data age, so it legitimately differs on every request.
        self._send(status, body, "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _wants_html(self):
        """True unless the caller has explicitly asked for JSON only.

        Browsers send ``text/html,...,*/*``; our own ``fetch()`` sends
        ``application/json``. A caller that sends nothing gets the readable
        page, which is the friendlier default.
        """
        accept = (self.headers.get("Accept") or "").lower()
        if "application/json" in accept and "text/html" not in accept:
            return False
        return True

    def _fail(self, status, message, hint=None):
        """Report a failure as HTML or JSON depending on who is asking."""
        if self._wants_html():
            self._send(status, render_error_page(status, message, hint),
                       "text/html; charset=utf-8")
        else:
            payload = {"error": message}
            if hint:
                payload["detail"] = hint
            self._send_json(payload, status)

    # -- routes ------------------------------------------------------------

    def do_GET(self):  # noqa: N802 - stdlib signature
        self._dispatch()

    def do_HEAD(self):  # noqa: N802 - stdlib signature
        self._dispatch()

    def do_POST(self):  # noqa: N802 - stdlib signature
        self._fail(HTTPStatus.METHOD_NOT_ALLOWED, "Η μέθοδος δεν υποστηρίζεται.")

    def _dispatch(self):
        started = time.perf_counter()
        try:
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            # keep_blank_values so that a bare "?force" (what app.js sends) works
            query = parse_qs(parsed.query, keep_blank_values=True)

            if path in ("/", "/index.html"):
                self._serve_shell()
            elif path == "/api/weather":
                self._serve_weather(force="force" in query)
            elif path == "/api/health":
                self._send_json(self.service.health())
            elif path == "/api/radar":
                self._serve_radar()
            elif path == "/geography.json":
                self._serve_geography()
            elif path in STATIC_FILES:
                self._serve_static(path)
            elif path == "/favicon.ico":
                self._serve_static("/favicon.svg")
            else:
                self._not_found()
        except (BrokenPipeError, ConnectionResetError):  # client went away
            log.debug("client disconnected during %s", self.path)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            log.exception("unhandled error for %s", self.path)
            try:
                self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "Κάτι πήγε στραβά.",
                           "Λεπτομέρειες: %s" % exc)
            except Exception:  # pragma: no cover
                pass
        finally:
            log.debug("%s %s in %.1fms", self.command, self.path,
                      (time.perf_counter() - started) * 1000.0)

    # -- individual responses ----------------------------------------------

    def _serve_shell(self):
        try:
            page = self.shell.render(self.config)
        except RuntimeError as exc:
            log.error("%s", exc)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                       render_error_page(500, "Το πρότυπο της σελίδας δεν βρέθηκε.",
                                         "Βεβαιωθείτε ότι το template.html είναι δίπλα στο app.py."),
                       "text/html; charset=utf-8")
            return
        body = page.encode("utf-8")
        self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", {
            "Cache-Control": "no-cache",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "worker-src 'self'; manifest-src 'self'; connect-src 'self'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        })

    def _maybe_alert(self, payload):
        """Fire a frost alert if one is due.

        Runs off ordinary traffic rather than a scheduler, which is why the
        suppression window matters: without it every request during a cold snap
        would send the same warning again. Never allowed to fail the request,
        and sent from its own thread so that a slow ntfy or Telegram cannot
        delay the page of whoever happened to trigger it.
        """
        if not notify.configured(self.config):
            return

        def send():
            try:
                notify.notify_frost(self.config, payload, datetime.now(timezone.utc))
            except Exception as exc:  # noqa: BLE001 - an alert is not worth a 500
                log.warning("frost alert failed: %s", exc)

        threading.Thread(target=send, name="frost-alert", daemon=True).start()

    def _serve_weather(self, force=False):
        try:
            snapshot = self.service.snapshot(force=force)
            payload = report_mod.build_report(snapshot, self.config)
            self._maybe_alert(payload)
        except report_mod.ReportError as exc:
            log.error("cannot build report: %s", exc)
            self._fail(HTTPStatus.SERVICE_UNAVAILABLE,
                       "Δεν ήταν δυνατή η λήψη δεδομένων καιρού.",
                       "Ελέγξτε τη σύνδεσή σας στο διαδίκτυο και δοκιμάστε ξανά σε λίγο.")
            return
        except sources.SourceError as exc:
            log.error("upstream failure: %s", exc)
            self._fail(HTTPStatus.SERVICE_UNAVAILABLE,
                       "Η υπηρεσία καιρού δεν απαντά.",
                       "Λεπτομέρειες: %s" % exc)
            return
        self._send_json(payload)

    def _serve_radar(self):
        """The decoded radar grid, or 503 until the first refresh lands.

        Already JSON bytes, so it goes out as-is and _send compresses it; a
        rainy frame is about 46 KB raw and about 10 KB gzipped.
        """
        payload = self.radar.payload() if self.radar else None
        if not payload:
            self._fail(HTTPStatus.SERVICE_UNAVAILABLE,
                       "Το ραντάρ δεν είναι ακόμα έτοιμο.",
                       "Δοκιμάστε ξανά σε λίγο.")
            return
        self._send(HTTPStatus.OK, payload, "application/json; charset=utf-8")

    def _serve_geography(self):
        """Lakes and borders for the radar, as JSON.

        Separate from /api/radar and cached hard, because it never changes
        while the radar payload is replaced every ten minutes. Riding along
        with it cost a dry hour 7 KB instead of 200 bytes, and a dry hour is
        the normal case here.
        """
        body = json.dumps({"lakes": geography.LAKES,
                           "borders": geography.BORDERS},
                          separators=(",", ":")).encode("utf-8")
        tag = _etag(body)
        if self.headers.get("If-None-Match") == tag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", tag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8",
                   extra_headers={"ETag": tag,
                                  "Cache-Control": "public, max-age=86400"})

    def _serve_static(self, path):
        _, content_type = STATIC_FILES[path]
        try:
            tag, body, packed = self.statics.get(path)
        except OSError:
            self._not_found()
            return

        # The page asks for /app.js?v=<release>, so that exact URL can never
        # change and the browser may keep it for good. Everything else - the
        # worker, the manifest, the icons, an unversioned request - revalidates
        # as before.
        query = parse_qs(urlsplit(self.path).query)
        cache_control = _cache_control(path, query)

        headers = {"ETag": tag, "Cache-Control": cache_control}
        if packed is not None:
            headers["Vary"] = "Accept-Encoding"
        if self.headers.get("If-None-Match") == tag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if packed is not None and _gzip_ok(self.headers.get("Accept-Encoding")):
            body = packed
            headers["Content-Encoding"] = "gzip"
        self._send(HTTPStatus.OK, body, content_type, headers, compress=False)

    def _not_found(self):
        self._fail(HTTPStatus.NOT_FOUND, "Η σελίδα δεν βρέθηκε.",
                   "Η διεύθυνση %s δεν υπάρχει." % self.path)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


def create_server(config, service=None, radar_instance=None):
    """Build a ready-to-serve HTTP server (used by the tests too).

    ``radar_instance`` is injected so the tests never touch RainViewer, and so
    a caller can pass ``None`` to run without the radar card at all.
    """
    handler = type("BoundHandler", (Handler,), {
        "service": service or sources.WeatherService(config),
        "shell": ShellCache(TEMPLATE),
        "statics": StaticCache(),
        "config": config,
        "radar": radar_instance,
    })
    return Server((config.host, config.port), handler)


def _split(value):
    """``"a, b"`` -> ``["a", "b"]``; ``None`` stays ``None`` so env config wins."""
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Τοπική σελίδα καιρού για τη Φλώρινα.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", help="interface to bind (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, help="TCP port")
    parser.add_argument("--place", help="town name shown on the page")
    parser.add_argument("--region", help="region name shown on the page")
    parser.add_argument("--lat", type=float, help="latitude")
    parser.add_argument("--lon", type=float, help="longitude")
    parser.add_argument("--refresh", type=int, help="client refresh interval, seconds")
    parser.add_argument("--cache-ttl", type=float, help="server-side forecast cache, seconds")
    parser.add_argument("--forecast-days", type=int, help="days in the daily forecast")
    parser.add_argument("--forecast-hours", type=int, help="hours in the chart and strip")
    parser.add_argument("--alert-areas", help="comma-separated Meteoalarm area names to match")
    parser.add_argument("--alert-emma-ids", help="comma-separated EMMA region codes to match")
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = sources.Config.from_env(
        host=args.host, port=args.port, place=args.place, region=args.region,
        lat=args.lat, lon=args.lon, refresh=args.refresh,
        cache_ttl=args.cache_ttl, forecast_days=args.forecast_days,
        forecast_hours=args.forecast_hours,
        alert_areas=_split(args.alert_areas),
        alert_emma_ids=_split(args.alert_emma_ids),
    )

    # The radar refreshes on its own thread and starts empty, so a slow or
    # failing RainViewer never delays the first page load: /api/radar simply
    # answers 503 until the first grid is built, and the card stays hidden.
    radar_instance = None
    if config.radar_enabled:
        radar_instance = radar.Radar(lat=config.lat, lon=config.lon,
                                     place=config.place)
        radar_instance.start()

    try:
        httpd = create_server(config, radar_instance=radar_instance)
    except OSError as exc:
        log.error("cannot bind %s:%s — %s", config.host, config.port, exc)
        log.error("Try another port:  python app.py --port 8080")
        return 1

    url = "http://%s:%d/" % (
        "localhost" if config.host in ("127.0.0.1", "0.0.0.0") else config.host,
        httpd.server_address[1])

    hostname = socket.gethostname()
    print("  Καιρός · %s" % config.place)
    print("  %s" % ("-" * 40))
    print("  Τοπικά          %s" % url)
    if config.host == "0.0.0.0":
        print("  Στο δίκτυο      http://%s:%d/" % (hostname, httpd.server_address[1]))
    print("  Ανανέωση        κάθε %d δευτερόλεπτα" % config.refresh)
    print("  Δεδομένα        Open-Meteo · Meteoalarm")
    print("  Διακοπή         Ctrl+C")
    print()
    sys.stdout.flush()  # so the banner survives being piped to a file

    if args.open:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=[url]).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Τέλος.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
