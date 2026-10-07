# Καιρός · Φλώρινα (Florina Weather)

[![tests](https://github.com/oxezz/florina-weather/actions/workflows/tests.yml/badge.svg)](https://github.com/oxezz/florina-weather/actions/workflows/tests.yml)

A small self-hosted weather page for **Φλώρινα, Δυτική Μακεδονία**, in Greek.
Python standard library only — no `pip install`, no API key, no build step.
Requires **Python 3.10 or newer** (3.9 is end-of-life).

**Live:** <https://florina-weather.wasmer.app/> — or run your own with the
command below.

```bash
cd florina-weather
python app.py
```

Then open **http://127.0.0.1:8000**.

---

## Deploying it

The repo is ready to push to GitHub and deploy from there. Nothing needs
installing, because the app has no dependencies.

**Any container host** — Render, Fly.io, Railway, a VPS. Point it at the repo;
it builds the [`Dockerfile`](Dockerfile) and runs. The image binds
`0.0.0.0:8000` and honours the generic `PORT` variable those platforms inject,
so there is nothing to configure.

**Wasmer Edge** works, and is verified in production: it runs the app
unmodified, including the threaded HTTP server. It does need the CA bundle
described below, which is why `cacert.pem` is committed.

**Static hosts will not work** — GitHub Pages, Netlify and Cloudflare Pages
serve files, and this is a running server that fetches and caches upstream data.

CI runs the whole suite on every push across Python 3.10, 3.12, 3.13 and 3.14
([`.github/workflows/tests.yml`](.github/workflows/tests.yml)).

### TLS trust store

Minimal hosts often ship no CA bundle at all. Wasmer Edge's Python runtime, for
instance, loads **zero** certificates into its default context, so every HTTPS
call fails with `CERTIFICATE_VERIFY_FAILED`.

So [`cacert.pem`](cacert.pem) — Mozilla's CA list, via
[curl.se](https://curl.se/ca/cacert.pem) — ships with the app. The precedence is:

1. `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` or `CURL_CA_BUNDLE`, if set and valid
2. the host's own store, *if it genuinely contains certificates*
3. the bundled `cacert.pem`

`/api/health` reports which one is active under `"tls"`. Refresh the bundle
occasionally by re-downloading it from curl.se.

That file is Mozilla's data, distributed under the **MPL-2.0**, and is not
covered by this project's MIT licence.

---

## Features

| | |
|---|---|
| **Current conditions** | temperature, apparent temperature, humidity, wind (speed, direction, arrow and Beaufort), gusts, precipitation, cloud cover, pressure and visibility |
| **Greeting & outfit** | a hello keyed to the hour and the weather, and a «Τι να φορέσω;» card suggesting a layer from the feels-like temperature, with umbrella, sunscreen, hat or ice warnings layered on top |
| **Moon & stargazing** | the lunar phase, how much of the disc is lit, moonrise and moonset, plus a verdict on whether tonight is worth looking up — cloud first, then haze, then moonlight |
| **48-hour outlook** | an inline SVG chart of temperature with precipitation-probability bars and night shading, plus a horizontally scrollable hour-by-hour strip |
| **7-day forecast** | per-day icon, description, min/max with a relative range bar, rainfall, UV and peak gusts |
| **Official warnings** | live **Meteoalarm / EMY** alerts for West Macedonia, colour-coded, shown as a banner at the top |
| **Air quality** | European AQI, PM2.5 / PM10 and pollen (grass, olive, ragweed, mugwort, birch, alder) |
| **Local conditions** | Hyper-local cards for the basin climate: **frost risk** for growers, **heating degree days** for the month, and the evening **wood-smoke** build-up. Each card is simply absent when it has nothing to say, so the whole panel disappears in summer |
| **Live updates** | the page refreshes itself in place — no reload, no flicker — and pauses while the tab is hidden |
| **Installable (PWA)** | A manifest and a service worker, so "Add to Home Screen" gives a standalone app with no URL bar — and the refresh button is always there for an immediate update |
| **Works offline** | The shell is cached, so a repeat launch is instant and an offline reload still shows the last weather with an «εκτός σύνδεσης» note rather than the browser's error page |
| **Loading skeletons** | 31 placeholders shaped like the real content, so the first paint already looks finished and nothing jumps when the data lands. They stay invisible for the first 350 ms, because a skeleton that flashes for two frames looks worse than none |
| **Light & dark** | an iOS-style segmented control (Αυτόματα / Φωτεινό / Σκοτεινό). The choice is remembered and can be linked with `?mode=dark` |
| **Liquid-glass surfaces** | translucent saturated blur, a specular rim along the top edge, a sheen out of the top-left corner, and a highlight that follows the pointer. All of it degrades gracefully — with no hover or no JavaScript the panels still read correctly |
| **Weather-aware backdrop** | shifts between clear-day, clear-night, cloud, rain, snow, storm and fog, in both light and dark |

### Appearance

The mode is independent of the weather theme: `html.mode-light` / `html.mode-dark`
sets the *material* (text, glass tint, shadows) and `body.theme-*` sets the
*backdrop hue*. Auto follows `prefers-color-scheme` live.

`?mode=light` or `?mode=dark` overrides the stored choice for one visit without
saving it, so a particular look is linkable.

| Dark | Light |
|:----:|:-----:|
| [![Dark](docs/dark.jpg)](docs/dark.jpg) | [![Light](docs/light.jpg)](docs/light.jpg) |

---

## How it works

```
   ┌──────────────────────┐
   │ Open-Meteo  forecast │──┐
   │ Open-Meteo  air      │──┤
   │ Open-Meteo  history  │──┼──►  sources.py  ──►  report.py  ──►  app.py  ──►  app.js
   │ Meteoalarm  warnings │──┘     fetch,           normalise        HTTP:        render,
   └──────────────────────┘         TTL cache,       into one         shell,       poll,
                                    stale-if-error   JSON doc         JSON API     update in place
```

| File | Purpose |
|------|---------|
| `app.py` | CLI, HTTP server, routing. Serves the shell, `/api/weather`, `/api/health` and an allow-list of static files |
| `sources.py` | Configuration plus the four upstream clients, the TTL cache and stale-if-error handling |
| `report.py` | Pure functions that turn raw upstream payloads into the document the UI consumes |
| `greek.py` | Greek vocabulary: WMO code → description + emoji, compass, Beaufort, UV/AQI bands, dates |
| `template.html` | The page shell. Only five placeholders, all server-filled |
| `theme.js` | Appearance bootstrap, loaded from `<head>` so light mode never flashes dark |
| `app.js` | Fetches `/api/weather` and renders it. Never injects upstream text as HTML |
| `style.css` | Glass material, light/dark tokens, the seven weather backdrops, layout |
| `sw.js` | Service worker: offline shell, and what lets Android offer "Install app" |
| `manifest.webmanifest` | Web app manifest — name, icons, standalone display |
| `Dockerfile` | Container image for Render / Fly / any container host |
| `cacert.pem` | Mozilla CA bundle, used when the host has no trust store |
| `tests/` | 362 tests, all offline |

### API

* `GET /` — the page
* `GET /api/weather` — the full JSON document; add `?force` to bypass the forecast cache
* `GET /api/health` — last success and last error per source

Errors are returned as HTML when a browser is navigating and as JSON when
`Accept: application/json` is sent, so the client can always parse the response.

---

## Configuration

Every flag has a matching `FLORINA_*` environment variable.

```bash
python app.py --port 8080 --place Φλώρινα --refresh 120 --verbose
FLORINA_PORT=8080 FLORINA_REFRESH=120 python app.py
```

| Flag | Env | Default | Meaning |
|------|-----|---------|---------|
| `--host` | `FLORINA_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` to reach it from other devices |
| `--port` | `FLORINA_PORT` | `8000` | TCP port |
| `--place` | `FLORINA_PLACE` | `Φλώρινα` | Town name on the page |
| `--region` | `FLORINA_REGION` | `Δυτική Μακεδονία` | Region name on the page |
| `--lat` / `--lon` | `FLORINA_LAT` / `FLORINA_LON` | `40.7822` / `21.4097` | Coordinates |
| `--refresh` | `FLORINA_REFRESH` | `600` | Client refresh interval, seconds. Open-Meteo's models update roughly every 15 minutes, so polling much faster only burns bandwidth |
| `--cache-ttl` | `FLORINA_CACHE_TTL` | `600` | Server-side forecast cache, seconds |
| `--forecast-days` | `FLORINA_FORECAST_DAYS` | `7` | Days in the daily forecast |
| `--forecast-hours` | `FLORINA_FORECAST_HOURS` | `48` | Hours in the chart and strip |
| — | `FLORINA_HISTORY_DAYS` | `45` | Days of daily history fetched for the heating-degree total |
| `--alert-areas` | `FLORINA_ALERT_AREAS` | `west macedonia,δυτική μακεδονία` | Which Meteoalarm areas to show |
| `--alert-emma-ids` | `FLORINA_ALERT_EMMA_IDS` | *(empty)* | Match warnings by EMMA region code instead of name |
| `--open` | — | off | Open the browser on start |
| `--verbose` | — | off | Debug logging |

**Retargeting another town** needs no code change:

```bash
python app.py --place Καστοριά --region Δυτική Μακεδονία \
              --lat 40.5167 --lon 21.2667 \
              --alert-areas "west macedonia"
```

### Warnings filter

Warnings arrive from the Greek Meteoalarm CAP feed and are matched against
`FLORINA_ALERT_AREAS`, accent- and case-insensitively (`ΔΥΤΙΚΗ ΜΑΚΕΔΟΝΙΑ` matches
`Δυτική Μακεδονία`). The Greek text of each warning is preferred over the English
one. If both `alert_areas` and `alert_emma_ids` are left empty, every current
warning is shown.

### Frost alerts

Off by default. With a transport configured, the frost card pushes a message
when it forecasts an actual freeze — `risk` stays quiet, because a night *near*
freezing is not worth a buzz at 3am.

```sh
# ntfy, no account needed
FLORINA_ALERT_WEBHOOK=ntfy
FLORINA_NTFY_URL=https://ntfy.sh/your-topic

# or Telegram
FLORINA_ALERT_WEBHOOK=telegram
FLORINA_TELEGRAM_TOKEN=123:abc
FLORINA_TELEGRAM_CHAT=42
```

The same alert is suppressed for `FLORINA_WEBHOOK_MIN_INTERVAL` seconds
(six hours by default), because the frost card stays lit for days once a cold
snap is in the forecast — without that, every request would send it again. An
*escalation* is never suppressed: going from `frost` to `severe` always gets
through.

Alert state lives in memory unless `FLORINA_WEBHOOK_STATE_PATH` names a writable
file, in which case it survives a restart. Both are tested, including the
read-only case.

ntfy is published through its JSON body form rather than headers: header values
must be latin-1, and a Greek title sent as one arrives mangled.

---

## Tests

```bash
python -m unittest discover -s tests -t .
```

All 362 tests run offline: upstream responses are replaced by fixtures, and the
HTTP tests start a real server on an ephemeral port with an injected opener.

## Greek wording

A few labels were deliberately chosen over the obvious alternative:

| Label | Why |
|-------|-----|
| **Ριπές ανέμου** | a *gust* is a ριπή ανέμου. "Ροή ανέμου" would be a wind **flow** — a different quantity, and not what the card shows |
| **Αίσθηση** | the card label is a noun phrase ("feels like"); "Αίσθητη" alone is a dangling adjective |
| **Υετός** | the card totals rain + showers + **snow**, so "Βροχή" would be wrong whenever it snows. Υετός is the term EMY uses |
| **Μπφ** | the conventional Greek abbreviation for μποφόρ |

---

## Notes

Design decisions, traps and the reasoning behind them live in
[`docs/DESIGN.md`](docs/DESIGN.md).


## Data

Weather and air-quality data from [Open-Meteo](https://open-meteo.com) — free,
no API key. Weather warnings from the official
[Meteoalarm](https://www.meteoalarm.org) CAP feed, as issued by EMY.

## Deferred

Known but unbuilt work, with its blockers, is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Licence

[MIT](LICENSE) © 2026 oxezz.
