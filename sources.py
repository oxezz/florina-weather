# -*- coding: utf-8 -*-
"""Upstream data sources: HTTP fetching and caching.

This module owns the network. It knows nothing about Greek labels, HTML or
rendering — it returns raw (but validated) JSON structures and lets
:mod:`report` shape them. Every network call is injectable, so the tests can
run the whole stack without touching the internet.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("florina.sources")

USER_AGENT = "florina-weather/2.0 (+local; python-urllib)"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# TLS trust
# --------------------------------------------------------------------------
#
# Minimal container images and WebAssembly runtimes often ship no CA bundle at
# all, which makes every HTTPS call fail with CERTIFICATE_VERIFY_FAILED. That
# is exactly what happened on Wasmer Edge, where the default context loaded
# zero certificates. So we ship Mozilla's CA list with the app and fall back to
# it whenever the host has nothing usable of its own.

CA_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
BUNDLED_CA = os.path.join(BASE_DIR, "cacert.pem")

_context = None
_context_source = None


def _remember(context, source):
    global _context, _context_source
    _context, _context_source = context, source
    return context


def ssl_context():
    """A verifying TLS context that works even without a system CA store.

    Precedence: an explicit environment override, then the host's own store if
    it genuinely contains certificates, then the bundle shipped with the app.
    """
    if _context is not None:
        return _context

    for name in CA_ENV_VARS:
        path = os.environ.get(name)
        if path and os.path.isfile(path):
            return _remember(ssl.create_default_context(cafile=path),
                             "%s=%s" % (name, path))

    try:
        system = ssl.create_default_context()
        if system.cert_store_stats().get("x509", 0) > 0:
            return _remember(system, "system store")
    except Exception as exc:  # noqa: BLE001 - a broken store is not fatal
        log.debug("could not inspect the system CA store: %s", exc)

    if os.path.isfile(BUNDLED_CA):
        return _remember(ssl.create_default_context(cafile=BUNDLED_CA),
                         "bundled cacert.pem")

    log.warning("no CA bundle available; outbound HTTPS will probably fail")
    return _remember(ssl.create_default_context(), "system default (empty)")


def tls_source():
    """Which trust store is in use, for ``/api/health``."""
    ssl_context()
    return _context_source


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ALERTS_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{country}"

FORECAST_CURRENT = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
    "precipitation,rain,showers,snowfall,weather_code,cloud_cover,"
    "pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,"
    "wind_gusts_10m"
)

FORECAST_HOURLY = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "precipitation_probability,precipitation,weather_code,is_day,"
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,uv_index,cloud_cover,"
    "visibility"
)

FORECAST_DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "apparent_temperature_max,apparent_temperature_min,precipitation_sum,"
    "precipitation_probability_max,sunrise,sunset,daylight_duration,"
    "sunshine_duration,uv_index_max,wind_speed_10m_max,wind_gusts_10m_max,"
    "wind_direction_10m_dominant"
)

AIR_CURRENT = (
    "european_aqi,pm2_5,pm10,alder_pollen,birch_pollen,grass_pollen,"
    "mugwort_pollen,olive_pollen,ragweed_pollen"
)


class SourceError(RuntimeError):
    """Raised when an upstream source cannot be read."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_list(name, default):
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    """Runtime settings, populated from CLI flags / environment variables."""

    lat: float = 40.7822
    lon: float = 21.4097
    place: str = "Φλώρινα"
    region: str = "Δυτική Μακεδονία"
    timezone: str = "Europe/Athens"
    host: str = "127.0.0.1"
    port: int = 8000
    refresh: int = 600          # client refresh interval; models update ~15 min
    cache_ttl: float = 600.0    # forecast cache lifetime
    air_cache_ttl: float = 900.0
    alerts_cache_ttl: float = 300.0
    max_stale: float = 6 * 3600.0
    timeout: float = 20.0
    forecast_days: int = 7
    forecast_hours: int = 48
    alert_country: str = "greece"
    alert_areas: list = field(default_factory=lambda: ["west macedonia", "δυτική μακεδονία"])
    alert_emma_ids: list = field(default_factory=list)

    @classmethod
    def from_env(cls, **overrides):
        """Build a config from ``FLORINA_*`` environment variables.

        Explicit keyword overrides (from the CLI) win over the environment.
        """
        cfg = cls(
            lat=_env_float("FLORINA_LAT", cls.lat),
            lon=_env_float("FLORINA_LON", cls.lon),
            place=os.environ.get("FLORINA_PLACE", cls.place),
            region=os.environ.get("FLORINA_REGION", cls.region),
            timezone=os.environ.get("FLORINA_TZ", cls.timezone),
            host=os.environ.get("FLORINA_HOST", cls.host),
            # PaaS platforms (Render, Fly, Heroku) inject a generic PORT;
            # FLORINA_PORT still wins when both are set.
            port=_env_int("FLORINA_PORT", _env_int("PORT", cls.port)),
            refresh=_env_int("FLORINA_REFRESH", cls.refresh),
            cache_ttl=_env_float("FLORINA_CACHE_TTL", cls.cache_ttl),
            air_cache_ttl=_env_float("FLORINA_AIR_CACHE_TTL", cls.air_cache_ttl),
            alerts_cache_ttl=_env_float("FLORINA_ALERTS_CACHE_TTL", cls.alerts_cache_ttl),
            timeout=_env_float("FLORINA_TIMEOUT", cls.timeout),
            forecast_days=_env_int("FLORINA_FORECAST_DAYS", cls.forecast_days),
            forecast_hours=_env_int("FLORINA_FORECAST_HOURS", cls.forecast_hours),
            alert_country=os.environ.get("FLORINA_ALERT_COUNTRY", cls.alert_country),
            alert_areas=_env_list("FLORINA_ALERT_AREAS", cls().alert_areas),
            alert_emma_ids=_env_list("FLORINA_ALERT_EMMA_IDS", ()),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg

    def title(self):
        return "Καιρός · %s" % self.place


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def get_json(url, params=None, timeout=20.0, attempts=3, opener=None):
    """GET a URL and decode the JSON body.

    ``opener`` is a callable taking ``(url, timeout)`` and returning bytes;
    it exists purely so tests can replace the network.
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if opener is not None:
                raw = opener(url, timeout)
            else:
                raw = _urlopen(url, timeout)
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # pragma: no cover - body already consumed
                pass
            # 4xx (other than 429) means we sent a bad request: retrying is pointless.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SourceError("HTTP %s from %s: %s" % (exc.code, url, detail)) from exc
            last_error = SourceError("HTTP %s from %s: %s" % (exc.code, url, detail))
        except Exception as exc:  # URLError, timeout, bad JSON, ...
            last_error = SourceError("%s: %s" % (type(exc).__name__, exc))
        if attempt < attempts:
            time.sleep(0.6 * attempt)
    raise last_error or SourceError("could not fetch %s" % url)


def _urlopen(url, timeout):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout,
                                context=ssl_context()) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


@dataclass
class Entry:
    value: object
    stored_at: float          # time.monotonic()
    stored_wall: datetime     # for display


class Cache:
    """A tiny thread-safe cache with per-key TTLs.

    The important behaviour is *stale-if-error*: when an upstream call fails we
    would rather serve slightly old data than show the user an error page.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}

    def get(self, key):
        with self._lock:
            return self._entries.get(key)

    def put(self, key, value):
        entry = Entry(value, time.monotonic(), datetime.now(timezone.utc))
        with self._lock:
            self._entries[key] = entry
        return entry

    def drop(self, key):
        """Forget one entry, forcing the next read to hit the network."""
        with self._lock:
            return self._entries.pop(key, None)

    def age(self, entry):
        if entry is None:
            return None
        return max(0.0, time.monotonic() - entry.stored_at)


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


class WeatherService:
    """Fetches, caches and reports on the three upstream sources."""

    def __init__(self, config=None, opener=None, clock=None):
        self.config = config or Config()
        self._opener = opener
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = Cache()
        self._lock = threading.Lock()
        self.last_error = {}
        self.last_success = {}

    # -- individual sources -------------------------------------------------

    def _forecast_params(self):
        cfg = self.config
        return {
            "latitude": cfg.lat,
            "longitude": cfg.lon,
            "current": FORECAST_CURRENT,
            "hourly": FORECAST_HOURLY,
            "daily": FORECAST_DAILY,
            "timezone": cfg.timezone,
            "forecast_days": cfg.forecast_days,
            "wind_speed_unit": "kmh",
        }

    def _air_params(self):
        cfg = self.config
        return {
            "latitude": cfg.lat,
            "longitude": cfg.lon,
            "current": AIR_CURRENT,
            "timezone": cfg.timezone,
        }

    def fetch_forecast(self):
        return get_json(FORECAST_URL, self._forecast_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_air(self):
        return get_json(AIR_URL, self._air_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_alerts(self):
        url = ALERTS_URL.format(country=self.config.alert_country)
        return get_json(url, None, timeout=self.config.timeout, opener=self._opener)

    # -- caching ------------------------------------------------------------

    def _cached(self, key, ttl, loader):
        """Return ``(value, age_seconds, stale, error)``."""
        entry = self.cache.get(key)
        age = self.cache.age(entry)
        if entry is not None and age is not None and age < ttl:
            return entry.value, age, False, None
        try:
            value = loader()
        except Exception as exc:  # noqa: BLE001 - deliberately broad: never 500 the page
            self.last_error[key] = str(exc)
            if entry is not None and age is not None and age < self.config.max_stale:
                log.warning("serving stale %s (age %.0fs): %s", key, age, exc)
                return entry.value, age, True, str(exc)
            raise
        self.last_success[key] = self._clock()
        self.last_error.pop(key, None)
        entry = self.cache.put(key, value)
        return value, 0.0, False, None

    # -- public API ---------------------------------------------------------

    def snapshot(self, force=False):
        """Collect every source into one dict describing what we have.

        Never raises for a single source: a failing source is recorded in
        ``errors`` and reported as ``None`` so the page still renders.
        """
        cfg = self.config
        if force:
            # A manual refresh only needs to re-read the forecast; the slower
            # sources (air quality, alerts) keep their own TTLs.
            with self._lock:
                self.cache.drop("forecast")

        forecast, forecast_age, forecast_stale, forecast_error = self._cached(
            "forecast", cfg.cache_ttl, self.fetch_forecast)

        air = air_age = None
        air_error = None
        try:
            air, air_age, _air_stale, air_error = self._cached(
                "air", cfg.air_cache_ttl, self.fetch_air)
        except Exception as exc:  # noqa: BLE001
            air_error = str(exc)

        alerts = alerts_age = None
        alerts_error = None
        try:
            alerts, alerts_age, _alerts_stale, alerts_error = self._cached(
                "alerts", cfg.alerts_cache_ttl, self.fetch_alerts)
        except Exception as exc:  # noqa: BLE001
            alerts_error = str(exc)

        errors = {}
        for name, message in (("forecast", forecast_error),
                              ("air", air_error),
                              ("alerts", alerts_error)):
            if message:
                errors[name] = message

        return {
            "forecast": forecast,
            "air": air,
            "alerts": alerts,
            "ages": {
                "forecast": forecast_age,
                "air": air_age,
                "alerts": alerts_age,
            },
            "errors": errors,
            "stale": bool(forecast_stale),
            "fetched_at": self._clock(),
        }

    def health(self):
        """Small status object for ``/api/health``."""
        return {
            "ok": not self.last_error,
            "tls": tls_source(),
            "last_success": {k: v.isoformat() for k, v in self.last_success.items()},
            "last_error": dict(self.last_error),
        }
