# Design notes

Why the parts of this app are built the way they are. The
[README](../README.md) covers what it does and how to run it; this file covers
the reasoning and the traps, most of which were found the hard way.

* **Timezone.** `report.resolve_timezone()` prefers the IANA database, but a stock
  Windows Python has none (`ZoneInfo` raises, because `tzdata` is a separate
  package). It then falls back to the `utc_offset_seconds` value Open-Meteo
  returns for the coordinates, which is correct for the forecast window. Which
  source was used is reported as `status.timezone_source`.
* **Installing it.** On Android Chrome the site offers "Install app"; on iOS use
  Share → Add to Home Screen. Either way you get a proper icon and a standalone
  window with no URL bar.

  The service worker is deliberately conservative, because stale weather is
  worse than none: `/api/weather` and navigations are **network-first**, cached
  only as an offline fallback, while the shell uses **stale-while-revalidate**
  so repeat launches are instant and a deploy is picked up on the next load.
  Bump `CACHE_VERSION` in `sw.js` only when something must never be served
  stale. The worker registers over https, and on loopback so local development
  behaves the same.
* **Local conditions.** Florina sits in a basin at ~660 m, which gives the town a
  climate of its own. Three readings of data we already fetch, each shown only
  when it means something:

  - **Frost risk** — two sensors, because they disagree. The 2 m air minimum
    *and* the ground surface (`soil_temperature_0cm`); the colder of the two
    sets the warning, and both are reported. On a clear calm night the surface
    radiates heat away and runs 1–3 °C below the air, so it can freeze while the
    air is still positive — the radiation frost that catches low crops such as
    peppers. Bands at 2 / 0 / −2 / −4 °C; −2 °C is roughly where flowering fruit
    trees start to suffer, −4 °C is a crop-damaging freeze. Absent whenever no
    night comes near zero.

    Only the **0 cm** layer is a frost sensor. At 6 cm the soil sits 5–8 °C
    warmer than the surface, which is root-zone warmth rather than frost risk.
  - **Heating degree days** — `18 °C − daily mean`, totalled for the month to
    date. Uses a daily-only history call (~2 KB) so it costs one extra request.
  - **Wood smoke** — the peak PM2.5 between 18:00 and 02:00, plus the wind that
    lets it pool. Thresholds are the WHO 2021 24-hour guideline (15 µg/m³) and
    the EU daily limit (25), so it only appears on a genuinely polluted evening.
    The wind decides the *framing*, not the severity: under 12 km/h it reads as
    «αιθαλομίχλη» pooling in the basin, with the cleanest hour of the next day;
    above that the same PM2.5 is particulate blowing through, so it is labelled
    «αυξημένα σωματίδια» and the ventilation advice is dropped.
  - **Thermal inversion** — the reason Florina is colder than the villages
    above it. A standard atmosphere loses 0.65 °C per 100 m, so the card
    compares the town with a **1073 m point about 3 km south-west** and reports
    how much warmer the slope is than that rule predicts. Everything is in the
    anomaly, measured in °C, and the card says «πιθανή» rather than asserting an
    inversion when the models straddle it by more than the anomaly itself.

    Note the comparison point's height is Open-Meteo's own DEM reading, which
    disagrees with third-party elevation tools by several hundred metres. Since
    Open-Meteo also produces the temperatures, only its figure is usable here.

  None of it is invented: every number is a straight reading of the forecast,
  the air-quality call or the history call, and a failing source drops its own
  card rather than breaking the page.
* **`[hidden]` needs its guard.** The stylesheet carries
  `[hidden] { display: none !important; }`, because the attribute only works via
  the browser's own `[hidden] { display: none }` — and *any* author rule that
  sets `display` silently outranks it. Without the guard, `.local-card` and
  `.chip` stayed on screen with blank contents while the attribute insisted they
  were hidden.
* **Model agreement.** The daily cards carry a ± figure from four models —
  `best_match`, `icon_eu` (DWD, 7 km), `ecmwf_ifs025` and `gfs_seamless`. In a
  basin they routinely disagree by 4–6 °C, which is more than a typical day's
  change, so a single number would be hiding the interesting part.

  `best_match` is included deliberately even though it is a composite: it is what
  the headline forecast comes from, and leaving it out let the displayed
  temperature fall **outside** the range the spread implied.

  AROME and ICON-D2 are absent because neither covers Greece — both return
  "no data is available for this location". This measures *inter-model* spread,
  which is the structural uncertainty that matters in mountains. A single-model
  ensemble would understate it badly: ICON-EU's own 40 members spread about
  1 °C, while the models disagree by 4–6 °C.
* **The hourly strip is a chart.** Each card carries a rain-probability gauge: a
  22 px bar scaled from `--rain`, so across 48 adjacent cards the bars line up
  into one continuous picture of the day. The percentage is still printed, and
  the gauge is `aria-hidden` because it repeats what the text already says. The
  strip uses `scroll-snap-type: x mandatory` with `overscroll-behavior-x:
  contain`, so a fling settles card by card and never triggers the browser's
  back gesture at the edges.
* **The moon comes from Open-Meteo after all.** `daily=moon_phase` returns a
  fraction of the synodic month — 0 new, 0.25 first quarter, 0.5 full, 0.75
  last. It is daily-only: `current` and `hourly` both reject it. Illumination is
  derived rather than fetched, as `(1 − cos 2π·phase) / 2`.
* **Καληνύχτα is never a greeting.** In Greek it is a farewell, so a page must
  not open with it. Before noon the greeting is «Καλημέρα», after it
  «Καλησπέρα», and there is a test asserting no hour produces anything else.
* **The stargazing verdict leads with cloud**, because cloud is what actually
  stops you seeing anything. Haze and moonlight only demote a clear sky, which
  is why a clear night with a full moon reads «καθαρός ουρανός, αλλά φωτεινό
  φεγγάρι» rather than «ιδανικές συνθήκες».
* **Caching.** Upstream calls are cached server-side and served stale (up to six
  hours) if the network fails, so a brief outage shows slightly old data instead
  of an error page.
* **Mobile performance.** Scroll jank on phones — Firefox especially — comes from
  work the browser redoes on *every* frame. Two culprits were removed:

  - `background-attachment: fixed` repaints the whole viewport per frame. The
    backdrop is now a single `position: fixed` layer that the compositor reuses.
  - A `backdrop-filter` on all thirteen panels and cards meant thirteen blur
    surfaces re-blurring the moving backdrop every frame. Blur now lives inside
    `@media (hover: hover) and (pointer: fine)`, so a phone never pays for it,
    at half the radius it used before.

  The glass still reads as glass on touch, because what sells it is the
  translucent fill, the specular rim and the sheen — none of which cost
  per-frame work. Since the backdrop is a smooth gradient, blurring it was never
  visible anyway. A refresh also skips rebuilding the chart, the 48 hour tiles
  and the 7 day cards when that data has not changed.
* **Headless Chrome reports `prefers-reduced-transparency: reduce` by default.**
  Any screenshot taken through CDP is therefore the *no-blur* variant unless the
  preference is explicitly emulated back to `no-preference`. It is a real
  supported feature, not an unknown one, so the fallback block genuinely
  engages — worth knowing before concluding the blur "doesn't work".
* **The old EMY scraper is gone.** `oldportal.emy.gr` no longer responds, so the
  previous `extract_emy.py` / `emy_extract.py` scrapers were retired and then
  deleted. They pulled `meteoalarmJson` out of the EMY warning page with a regex;
  warnings now come straight from the official Meteoalarm CAP feed, which is the
  same upstream data EMY was republishing and needs no HTML scraping at all. The
  CAP shape they documented lives on in [`tests/fixtures.py`](../tests/fixtures.py).

  The HTML page dumps they produced were removed from the repository — that
  markup was EMY's, not this project's, so it did not belong under its MIT
  licence.
