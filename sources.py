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
from concurrent import futures
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("florina.sources")

USER_AGENT = "florina-weather/2.0 (+local; python-urllib)"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# The Greek government's open data portal publishes the EMY automatic station
# network. It runs on CKAN, and its datastore API answers without a token —
# which is the only free real-observation source found for this region.
# There is no station in Florina itself, so the nearest fresh one is used and
# labelled as such rather than passed off as the town.
DATAGOV_URL = "https://data.gov.gr/api/action"

# Only the columns the card uses. A full station row is ~60 fields of strings,
# so asking for these keeps 200 records to about 15 KB instead of 100.
STATION_FIELDS = (
    "yyyyMMddHHmm,Temp_Dry_5min,Temp_Dry_Min_5min,Temp_Dry_Max_5min,"
    "Rel_Hum_5min,Prec_Sum_1_5min,Wind_Speed_Avg_5min,Wind_Speed_Max_5min,"
    "Wind_Dir_Avg_5min,Press_Barometer_5min,Rad_Global_5min"
)
# About two days at the 15-minute cadence, which is enough for today's range.
STATION_ROWS = 200

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
    "pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)

FORECAST_HOURLY = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "precipitation_probability,precipitation,weather_code,is_day,"
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,uv_index,cloud_cover,"
    "visibility,soil_temperature_0cm"
)

FORECAST_DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "apparent_temperature_max,apparent_temperature_min,precipitation_sum,"
    "precipitation_probability_max,sunrise,sunset,daylight_duration,"
    "sunshine_duration,uv_index_max,wind_speed_10m_max,wind_gusts_10m_max,"
    "wind_direction_10m_dominant,"
    # Lunar data is daily-only. `moon_phase` is a fraction of the synodic
    # month: 0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter.
    "moon_phase,moonrise,moonset"
)

AIR_CURRENT = (
    "european_aqi,pm2_5,pm10,alder_pollen,birch_pollen,grass_pollen,"
    "mugwort_pollen,olive_pollen,ragweed_pollen"
)

# Hourly PM2.5 is what drives the wood-smoke indicator; it rides along on the
# air call we already make, so it costs no extra request.
AIR_HOURLY = "pm2_5,pm10,european_aqi"

# Heating degree days need daily means going back far enough to cover a full
# month. Daily-only, so the payload stays under 2 KB.
HISTORY_DAILY = "temperature_2m_mean,temperature_2m_min,temperature_2m_max"

# Models used both to score their disagreement and to put an error bar on the
# inversion index. `best_match` is included deliberately: it is what the
# headline forecast is drawn from, so leaving it out let the displayed
# temperature fall outside the range the spread implied.
# AROME and ICON-D2 are absent because neither covers Greece.
DEFAULT_MODELS = ("best_match", "icon_eu", "ecmwf_ifs025", "gfs_seamless")


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
    # Absolute base for Open Graph URLs. Scrapers want absolute ones; when this
    # is empty the tags fall back to paths, which most of them resolve anyway.
    public_url: str = ""
    timezone: str = "Europe/Athens"
    host: str = "127.0.0.1"
    port: int = 8000
    refresh: int = 600          # client refresh interval; models update ~15 min
    cache_ttl: float = 600.0    # forecast cache lifetime
    air_cache_ttl: float = 900.0
    history_cache_ttl: float = 1800.0
    alerts_cache_ttl: float = 300.0
    # The longest a snapshot will wait for its slowest source. Open-Meteo is
    # usually under a second but occasionally takes twenty, and the forecast
    # is ready long before that. A source that misses the deadline is simply
    # absent this time — it keeps running and the cache picks it up next load.
    snapshot_deadline: float = 15.0
    max_stale: float = 6 * 3600.0
    timeout: float = 20.0
    # How long a source that just failed is left alone. Without this, every
    # page view retries every source during an outage or a 429.
    failure_ttl: float = 45.0
    # The shortest gap between two honoured `?force` refreshes.
    force_min_interval: float = 45.0
    forecast_days: int = 7
    forecast_hours: int = 48
    history_days: int = 35      # covers any month-to-date window (31 days + margin)

    # Terrain comparison point for the inversion index: high ground close enough
    # to share the valley's weather. 1073 m, about 3 km south-west of town.
    slope_lat: float = 40.7622
    slope_lon: float = 21.3797
    slope_name: str = "υψίπεδο 1073 μ."
    compare_models: list = field(default_factory=lambda: list(DEFAULT_MODELS))
    terrain_cache_ttl: float = 900.0

    # Climate normals, for "colder than usual". Ten years of daily means from
    # the archive API is about 63 KB, so it is cached for a day: the value
    # changes once a year, not once an hour.
    normals_years: int = 10
    normals_cache_ttl: float = 86400.0

    # Snow on the ground up at the ski area. Coordinates are Open-Meteo's; the
    # elevation it reports for each is shown on the card.
    snow_points: list = field(default_factory=lambda: [
        {"key": "vigla", "name": "Βίγλα", "lat": 40.7722, "lon": 21.2682},
        {"key": "pisoderi", "name": "Πισοδέρι", "lat": 40.7833, "lon": 21.2500},
    ])
    snow_cache_ttl: float = 1800.0

    # A real observation, from the EMY automatic network via data.gov.gr.
    # 007 is Kastoria: the nearest station that is actually kept fresh, in the
    # next basin west. 230 (Vitsi) is closer but runs a day behind and sits at
    # 1700 m, so it reads nothing like the town. Set station_id = "" to disable.
    station_id: str = "007"
    station_name: str = "Καστοριά"
    station_distance: str = "30 χλμ. δυτικά"
    station_cache_ttl: float = 900.0
    # How stale a reading may be before the card says so rather than hiding it.
    station_max_age: float = 6 * 3600.0

    # Outbound frost alerts. Nothing is ever sent unless a transport is both
    # chosen and given somewhere to send to, so the defaults are silent.
    alert_webhook: str = ""             # "ntfy" or "telegram"
    ntfy_url: str = ""                  # e.g. https://ntfy.sh/florina-frost
    telegram_token: str = ""
    telegram_chat: str = ""
    # The frost card stays lit for days once a cold snap is forecast, so the
    # same alert is suppressed for this long.
    webhook_min_interval: float = 6 * 3600.0
    # Where the "already sent" record lives. Empty keeps it in memory, which
    # is lost on restart; a path survives one if the host allows writes.
    webhook_state_path: str = ""
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
            # Set this on a deployment so Open Graph URLs are absolute.
            public_url=os.environ.get("FLORINA_PUBLIC_URL", cls.public_url),
            alert_webhook=os.environ.get("FLORINA_ALERT_WEBHOOK", cls.alert_webhook),
            ntfy_url=os.environ.get("FLORINA_NTFY_URL", cls.ntfy_url),
            telegram_token=os.environ.get("FLORINA_TELEGRAM_TOKEN",
                                          cls.telegram_token),
            telegram_chat=os.environ.get("FLORINA_TELEGRAM_CHAT",
                                         cls.telegram_chat),
            timezone=os.environ.get("FLORINA_TZ", cls.timezone),
            host=os.environ.get("FLORINA_HOST", cls.host),
            # PaaS platforms (Render, Fly, Heroku) inject a generic PORT;
            # FLORINA_PORT still wins when both are set.
            port=_env_int("FLORINA_PORT", _env_int("PORT", cls.port)),
            refresh=_env_int("FLORINA_REFRESH", cls.refresh),
            cache_ttl=_env_float("FLORINA_CACHE_TTL", cls.cache_ttl),
            air_cache_ttl=_env_float("FLORINA_AIR_CACHE_TTL", cls.air_cache_ttl),
            history_cache_ttl=_env_float("FLORINA_HISTORY_CACHE_TTL",
                                         cls.history_cache_ttl),
            alerts_cache_ttl=_env_float("FLORINA_ALERTS_CACHE_TTL", cls.alerts_cache_ttl),
            timeout=_env_float("FLORINA_TIMEOUT", cls.timeout),
            forecast_days=_env_int("FLORINA_FORECAST_DAYS", cls.forecast_days),
            forecast_hours=_env_int("FLORINA_FORECAST_HOURS", cls.forecast_hours),
            history_days=_env_int("FLORINA_HISTORY_DAYS", cls.history_days),
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
        self._key_locks = {}
        self._failures = {}
        self._last_force = None
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
            "hourly": AIR_HOURLY,
            "forecast_days": 3,
            "timezone": cfg.timezone,
        }

    def _history_params(self):
        cfg = self.config
        return {
            "latitude": cfg.lat,
            "longitude": cfg.lon,
            "daily": HISTORY_DAILY,
            "past_days": cfg.history_days,
            "forecast_days": 1,
            "timezone": cfg.timezone,
        }

    def _terrain_params(self):
        """Two locations, several models, one request.

        Serves both the inversion index (valley against slope) and the model
        disagreement shown on the daily cards. `current` is deliberately not
        used: with several models it returns a single unsuffixed value, so the
        per-model comparison has to come from `hourly`.
        """
        cfg = self.config
        return {
            "latitude": "%s,%s" % (cfg.lat, cfg.slope_lat),
            "longitude": "%s,%s" % (cfg.lon, cfg.slope_lon),
            "hourly": "temperature_2m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "models": ",".join(cfg.compare_models),
            "forecast_days": cfg.forecast_days,
            "timezone": cfg.timezone,
        }

    def fetch_forecast(self):
        return get_json(FORECAST_URL, self._forecast_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_air(self):
        return get_json(AIR_URL, self._air_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_history(self):
        return get_json(FORECAST_URL, self._history_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_terrain(self):
        return get_json(FORECAST_URL, self._terrain_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def _normals_params(self):
        """Ten years ending last year, so the normal is a closed set."""
        cfg = self.config
        last = datetime.now(timezone.utc).year - 1
        return {
            "latitude": cfg.lat,
            "longitude": cfg.lon,
            "start_date": "%d-01-01" % (last - cfg.normals_years + 1),
            "end_date": "%d-12-31" % last,
            "daily": "temperature_2m_mean",
            "timezone": cfg.timezone,
        }

    def fetch_normals(self):
        return get_json(ARCHIVE_URL, self._normals_params(),
                        timeout=max(self.config.timeout, 45.0), opener=self._opener)

    def _snow_params(self):
        cfg = self.config
        return {
            "latitude": ",".join(str(p["lat"]) for p in cfg.snow_points),
            "longitude": ",".join(str(p["lon"]) for p in cfg.snow_points),
            "daily": ("snowfall_sum,snow_depth_max,"
                      "temperature_2m_min,temperature_2m_max"),
            "forecast_days": min(cfg.forecast_days, 7),
            "timezone": cfg.timezone,
        }

    def fetch_snow(self):
        return get_json(FORECAST_URL, self._snow_params(),
                        timeout=self.config.timeout, opener=self._opener)

    def fetch_station(self):
        """The latest observation from an EMY station, via data.gov.gr.

        Two steps, because CKAN needs a resource id rather than a package name:
        the package is read first and its datastore resource taken from that.
        Everything comes back as strings, with missing readings written as a
        run of slashes — see :func:`_reading`.
        """
        cfg = self.config
        package = get_json(
            "%s/package_show" % DATAGOV_URL,
            {"id": "emy-station-%s" % cfg.station_id},
            timeout=self.config.timeout, opener=self._opener)

        resource = None
        for entry in (package or {}).get("result", {}).get("resources", []):
            if entry.get("datastore_active"):
                resource = entry.get("id")
                break
        if not resource:
            raise RuntimeError("station %s has no datastore" % cfg.station_id)

        found = get_json(
            "%s/datastore_search" % DATAGOV_URL,
            {"resource_id": resource, "limit": STATION_ROWS,
             "fields": STATION_FIELDS, "sort": "yyyyMMddHHmm desc"},
            timeout=self.config.timeout, opener=self._opener)
        return (found or {}).get("result") or {}

    def fetch_alerts(self):
        url = ALERTS_URL.format(country=self.config.alert_country)
        return get_json(url, None, timeout=self.config.timeout, opener=self._opener)

    # -- caching ------------------------------------------------------------

    def _lock_for(self, key):
        """One lock per source, so a stampede at expiry becomes one fetch."""
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = self._key_locks[key] = threading.Lock()
            return lock

    def _recently_failed(self, key):
        when = self._failures.get(key)
        if when is None:
            return False
        return (self._clock() - when).total_seconds() < self.config.failure_ttl

    def _force_allowed(self):
        """Rate-limit ``?force`` so a held-down refresh cannot hammer upstream."""
        now = self._clock()
        with self._lock:
            if (self._last_force is not None and
                    (now - self._last_force).total_seconds()
                    < self.config.force_min_interval):
                return False
            self._last_force = now
            return True

    def _cached(self, key, ttl, loader, skip_ttl=False):
        """Return ``(value, age_seconds, stale, error)``.

        Three guards against leaning on upstream harder than we need to:

        * ``skip_ttl`` (a manual refresh) re-reads *without* discarding the
          entry we hold, so a refresh that fails still has something to serve.
          Dropping it first is what left the page with nothing at all.
        * a per-key lock collapses concurrent callers at expiry into one fetch.
        * a source that just failed is left alone for ``failure_ttl`` rather
          than being retried on every single page view.
        """
        entry = self.cache.get(key)
        age = self.cache.age(entry)

        if not skip_ttl and entry is not None and age is not None and age < ttl:
            return entry.value, age, False, None

        if not skip_ttl and self._recently_failed(key):
            if entry is not None and age is not None and age < self.config.max_stale:
                return entry.value, age, True, self.last_error.get(key)
            raise RuntimeError(self.last_error.get(key) or (key + " unavailable"))

        with self._lock_for(key):
            # Another thread may have refreshed while we waited for the lock.
            entry = self.cache.get(key)
            age = self.cache.age(entry)
            if not skip_ttl and entry is not None and age is not None and age < ttl:
                return entry.value, age, False, None
            try:
                value = loader()
            except Exception as exc:  # noqa: BLE001 - never 500 the page for this
                self.last_error[key] = str(exc)
                self._failures[key] = self._clock()
                if entry is not None and age is not None and age < self.config.max_stale:
                    log.warning("serving stale %s (age %.0fs): %s", key, age, exc)
                    return entry.value, age, True, str(exc)
                raise
            self.last_success[key] = self._clock()
            self.last_error.pop(key, None)
            self._failures.pop(key, None)
            entry = self.cache.put(key, value)
            return value, 0.0, False, None

    # -- public API ---------------------------------------------------------

    def snapshot(self, force=False):
        """Collect every source into one dict describing what we have.

        Never raises for a single source: a failing source is recorded in
        ``errors`` and reported as ``None`` so the page still renders.
        """
        cfg = self.config

        # A manual refresh re-reads the forecast without dropping it, and is
        # rate-limited so a held-down button cannot hammer upstream. The slower
        # sources keep their own TTLs.
        skip_forecast = bool(force) and self._force_allowed()

        jobs = (
            ("air", cfg.air_cache_ttl, self.fetch_air, False),
            ("history", cfg.history_cache_ttl, self.fetch_history, False),
            ("terrain", cfg.terrain_cache_ttl, self.fetch_terrain, False),
            ("alerts", cfg.alerts_cache_ttl, self.fetch_alerts, False),
            ("normals", cfg.normals_cache_ttl, self.fetch_normals, False),
            ("snow", cfg.snow_cache_ttl, self.fetch_snow, False),
            ("station", cfg.station_cache_ttl, self.fetch_station, False),
        )

        # One thread per source. They are independent, and fetching them in
        # sequence made a cold start cost the *sum* of timeouts rather than the
        # slowest one.
        results = {}
        pool = futures.ThreadPoolExecutor(max_workers=len(jobs) + 1)
        try:
            # The forecast is not optional: without it there is no page at all,
            # so it is started first and waited for however long it takes. A
            # deadline on this one turned a merely slow start into a 503 for
            # the first visitor, which is worse than making them wait.
            forecast_future = pool.submit(
                self._cached, "forecast", cfg.cache_ttl,
                self.fetch_forecast, skip_forecast)

            # Everything else is a bonus, so it gets the deadline. A source
            # that misses it keeps running and the cache picks it up next load.
            pending = {
                pool.submit(self._cached, key, ttl, loader, skip): key
                for key, ttl, loader, skip in jobs
            }
            done, running = futures.wait(pending, timeout=cfg.snapshot_deadline)
            for future in done:
                key = pending[future]
                try:
                    results[key] = future.result()
                except Exception as exc:  # noqa: BLE001 - one source, never the page
                    results[key] = (None, None, False, str(exc))
            for future in running:
                key = pending[future]
                results[key] = (None, None, False,
                                "no answer within %.0fs" % cfg.snapshot_deadline)
                log.warning("%s missed the %.0fs snapshot deadline",
                            key, cfg.snapshot_deadline)

            # Started first, waited for last, with no deadline of its own.
            try:
                results["forecast"] = forecast_future.result()
            except Exception as exc:  # noqa: BLE001 - the page reports it
                results["forecast"] = (None, None, False, str(exc))
        finally:
            # Do not wait: a slow optional source that does arrive still warms
            # the cache for the next request, it just does not hold this one up.
            pool.shutdown(wait=False)

        def taken(key):
            value, age, stale, error = results.get(key, (None, None, False, None))
            return value, age, error

        forecast, forecast_age, forecast_error = taken("forecast")
        air, air_age, air_error = taken("air")
        history, history_age, history_error = taken("history")
        terrain, terrain_age, terrain_error = taken("terrain")
        alerts, alerts_age, alerts_error = taken("alerts")
        normals, normals_age, normals_error = taken("normals")
        snow, snow_age, snow_error = taken("snow")
        station, station_age, station_error = taken("station")
        forecast_stale = results.get("forecast", (None, None, False, None))[2]

        errors = {}
        for name, message in (("forecast", forecast_error),
                              ("air", air_error),
                              ("history", history_error),
                              ("terrain", terrain_error),
                              ("alerts", alerts_error),
                              ("normals", normals_error),
                              ("snow", snow_error),
                              ("station", station_error)):
            if message:
                errors[name] = message

        return {
            "forecast": forecast,
            "air": air,
            "history": history,
            "terrain": terrain,
            "alerts": alerts,
            "normals": normals,
            "snow": snow,
            "station": station,
            "ages": {
                "forecast": forecast_age,
                "air": air_age,
                "history": history_age,
                "terrain": terrain_age,
                "alerts": alerts_age,
                "normals": normals_age,
                "snow": snow_age,
                "station": station_age,
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
