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
* **The solar card reads the daily total, not the hours so far.** Summing the
  remaining hourly radiation made every evening look like heavy cloud: at 21:59
  it read 0% and called a clear day «συννεφιά». Today's total is a daily variable on the
  forecast API, and the ten-year normal rides along in the archive call that
  already feeds the temperature comparison — so the ratio is the same at 3am
  as at noon, and it costs no extra request.
* **The dew point comes ready-made.** The Magnus formula in the original sketch
  is not needed: Open-Meteo publishes dew_point_2m. It is a better comfort
  measure than relative humidity, which reads 60% on both a dry 5 °C day and a
  sticky 25 °C one.
* **The mountain road is judged by the freezing level, not the town.** The Vigla
  pass sits at 1773 m, so rain in Florina can be snow up there with the valley
  none the wiser. reezing_level_height at or below the pass elevation, with
  precipitation forecast, is the signal — the frost card's logic one altitude up.

* **The inversion index understates inversions, and here is by how much.** The
  index compares two *modelled* temperatures, so if the model's valley value is
  wrong the index is wrong however neat the arithmetic. Florina's real
  observations settle it: EMY station **WMO 16613** sits **in the town** at
  40.783 N, 21.400 E, 662 m — NOAA ISD, Meteostat, EMY's own climatological
  atlas and a Greek NTUA thesis table all agree — and NOAA's ISD-Lite archive
  carries its hourly records.

  Comparing the archived forecast against 472 paired hours in January and July
  2024:

  | | mean (obs − fc) | mean abs | within 2 °C |
  |---|---|---|---|
  | night (00/03/06 UTC) | **−1.61 °C** | 2.63 | 47 % |
  | day (12/15/18 UTC) | **+1.11 °C** | 2.21 | 47 % |
  | all | −0.07 °C | 2.42 | 46 % |

  A forecast running **1.6 °C too warm at night in the valley** makes the
  valley-to-slope difference too small, so the inversion reads **weaker than it
  is**. That is the signature of a model under-resolving a basin's nocturnal
  cold pool — the very thing the index exists to detect.

  It is reported rather than corrected. A correction would be a fudge on a
  one-year, two-month sample, and the archive grid is 728 m against the
  station's 662 m, which once adjusted for makes the night bias nearer 2 °C
  rather than smaller. A correction worth applying needs several years and a
  slope station to check the other leg against; it is recorded in
  [`ROADMAP.md`](ROADMAP.md) rather than guessed at.

* **There is a Florina station, and the open-data portal does not carry it.**
  EMY operates WMO 16613 in the town, but data.gov.gr publishes only station
  **007 (Kastoria)** and **230 (Vitsi)** for this region. So the page shows
  those two as references while the station that would actually validate the
  model lives elsewhere — ISD-Lite for the archive, and nothing conveniently
  live.

* **Coordinates that look right and are not.** data.gov.gr's spatial bboxes are
  GeoNames *place* extents, not instrument sites: the "ΜΑΚΕΔΟΝΙΑ" dataset
  carries the Thessaloniki city box, which excludes the airport where its
  station actually sits. `emy.gr`'s own embedded registry lists "ΒΙΤΣΙ" at a
  point that reverse-geocodes to a village at 914 m, nowhere near the station,
  and its "FLORINA" entry is likewise a hamlet. Both are forecast localities,
  neither usable as a station position.

* **Vitsi has no published coordinates at all.** It is absent from NOAA ISD,
  Meteostat, EMY's public GeoServer layers, OSM and EMY's synoptic list, and
  carries no WMO number. Its barometer implies roughly 1800 m — about 1160 m
  above Kastoria — which rules out the ski-centre base at 1547 m. EMY's own
  open-data register lists station coordinates as available on request.

* **The "normal" baseline is calibrated, not raw.** The ten-year baseline comes
  from ERA5, and ERA5 runs warm at Florina. NOA publishes fourteen years of
  daily records from a station **in the town** under **CC BY 4.0**
  (`stations_data/florina.csv`, 5097 days, 2010–2023), so the gap is measurable
  rather than a matter of opinion:

  | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | year |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | −0.97 | +0.33 | +0.35 | +0.06 | −0.40 | −1.03 | −1.31 | **−1.39** | −1.04 | −0.98 | −0.63 | −0.92 | **−0.66** |

  Measured minus ERA5, °C. Nine months are too warm, worst in August. Without
  this the card said almost every day was cooler for its date than it really
  was, by up to a degree and a half. `ERA5_BIAS_MONTHLY` in
  [`report.py`](../report.py) applies it; `build_normal` returns the `bias` it
  used so the number is inspectable rather than hidden.

  This is calibration with evidence, not a fudge, and it is the one case where
  the network bias against a model is *favourable* to remove rather than
  report: the correction makes the page agree with the town.

* **The archive is offline only, deliberately.** The CC BY 4.0 bundle is 5.3 MB
  and ends at 2023, so it is not a live source and never enters the runtime
  path — the whole point of this project is that it stays light. It was
  downloaded once to derive the twelve numbers above, and those twelve numbers
  are all that ships. The measured record also settles questions that were
  guesswork before:

  * **The basin signature, measured.** Mean daily swing **12.3 °C**, median
    12.9, p90 18.3, p99 21.1, and 158 days over 20 °C. A real cold pool.
  * **Frost is not an edge case.** 1139 of 5097 days (22.3 %) have a sub-zero
    minimum: January 317, December 276, February 224, March 153, November 114,
    April 31, October 23, May 1.
  * **The trend is small.** +0.23 °C between 2010–2014 and 2019–2023, which
    agrees with the published finding of no significant trend at Florina over
    1960–2004 while the rest of Macedonia warmed.
  * **The data has spikes.** One day in the CSV shows an 87.9 °C swing — a
    sensor glitch, not a climate. Anything consuming this must sanity-check
    rather than trust.

* **Licensing, and why scraping was the wrong instinct.** Three sources, three
  different positions:

  | Source | Terms |
  |---|---|
  | NOA archive on data.gov.gr | **CC BY 4.0** — attribute, nothing else |
  | EMY `newportal.hnms.gr` | CC BY-NC-ND 4.0 — attribution required; NC satisfied by a free, unmonetised page; ND concerns redistributing modified copies, not displaying a measurement |
  | `penteli.meteo.gr` pages | the site's terms require **consent and a logo**, so they are not usable as a scrape target — and the CC BY 4.0 dataset makes scraping them pointless anyway |

  The lesson worth keeping: when a site's terms look restrictive, check whether
  the same organisation publishes the same data on the national open-data
  portal. NOA does, under a licence that asks for nothing but credit.

* **The scraping decision was reversed on purpose, for one source only.** The
  2.0 rewrite retired EMY scrapers in favour of the Meteoalarm CAP feed, and
  that reasoning still holds for warnings. It does not hold for a station
  reading, because there is no substitute: the town's own observation exists
  only as server-rendered HTML on EMY's portal.

  Three things make it defensible where the old scrapers were not:

  1. **The parse target is a machine format, not markup.** Each row carries a
     raw AUTO report in a tooltip attribute —
     `613 071200Z AUTO VRB01KT //// // ////// 24/01 Q1021 RE//=` — which is a
     fixed METAR-like grammar. The table cells around it are what changes; the
     report does not.
  2. **Humidity is computed, not scraped.** It comes from the dew point by the
     Magnus formula, so one fewer column has to survive a redesign.
  3. **It degrades to nothing.** If the page changes shape the parse returns
     `None`, the card hides, and the open-data references take over as primary.
     A broken scrape cannot break the page.

  It is also the only station that is *in Florina*, which matters more than the
  maintenance cost: a proxy 30 km away with a five-hour lag is a worse answer
  to "what is it doing outside" than the town itself. Attribution sits in the
  footer.

* **The mountain is one card with two seasons, not two cards with one.** The
  road status and the snow depth were separate local cards that both hid
  themselves out of season. They answer the same question on the same day and
  read the same forecast at the same point, so they are now one card that
  switches: snow and road from November to March, cool air and hiking the rest
  of the year. Fresh snow switches it too, not just lying snow — a dump in
  October is a ski day before anything settles.

  The summer half is the one that did not exist. Its headline is the
  **temperature gap against the town**, which is the actual reason to drive up:
  ~1180 m of relief at 0.65 °C/100 m is about 7.7 °C, and the model currently
  reads 6.9 °C on a mild October afternoon.

  **The station on the ridge is not usable for this**, and that is the finding
  that shaped the card. EMY's Vitsi feed arrives in batches that run from hours
  to **more than a day** behind, so "the latest reading" can be yesterday
  lunchtime. Conditions come from the model; the station is an archive.

* **Snow had to stop hiding itself.** `build_snow` used to return `None` when
  there was nothing to report, which was right when it was its own card and
  wrong the moment the mountain card needed the same forecast in July — when
  the depth is legitimately zero and the temperature gap is the whole point.
  The gate moved from the data to the caller.

* **Road risk is counted from now, not from midnight.** The hourly window
  started at 00:00, so this evening's card was counting the morning's snowfall
  towards tonight's risk. A clearing day read like a blizzard and a clearing
  evening read calm with snow still coming.

* **EMY's station timestamps are UTC, and reading them as local was a bug.**
  The field is `yyyyMMddHHmm` with no separators and no zone, and it is UTC.
  Every station on the page was therefore shown with the wrong observation
  time *and* an age three hours too large — which also meant the staleness
  guard could hide a reading three hours before it was actually stale.

  Two independent lines established it, which is why it is worth trusting:

  1. EMY's own portal prints those same timestamps, and each row's tooltip
     carries the raw AUTO report ending in `Z` — `613 071200Z AUTO …`. When
     NOA's town station read 14:00, that page read 11:00.
  2. Putting the station's diurnal cycle against a local-time model cycle
     lines the two up **only after a three-hour shift, at r = 0.99 against
     0.96 unshifted**. Before the correction the model-versus-station error
     swung from +3.5 °C to −6.2 °C across the day, which is the shape of a
     clock disagreement rather than of physics; after it, the mean absolute
     error fell from **3.11 °C to 2.14 °C** and the curve flattened.

  The row-of-slashes lesson repeats here in a second form: a timestamp with no
  zone is not a timestamp until you know which zone it is in.

* **The slope leg of the inversion is biased too, in the same direction.**
  The valley leg was checked against Florina's station. The other leg is
  EMY's station on the Vitsi ridge, which publishes no coordinates, so a model
  point was chosen by elevation instead — its own barometer implies ~1800 m,
  and the point used is 1759 m. Comparing 751 paired hours of archived
  forecast against the measured record:

  | | value |
  |---|---|
  | mean error (observed − modelled) | **−1.78 °C** |
  | mean absolute | 2.14 °C |
  | p10 / p90 | −3.93 / +0.54 |
  | within 2 °C | 49 % |

  The model runs about **1.8 °C too warm at 1760 m**, and the error is
  one-sided — it is almost never too cold. Worst in the evening (−4.0 °C at
  19:00), best around dawn.

  **This is better news for the index than the valley result alone suggested.**
  The valley is modelled too warm at night by ~1.6 °C and the ridge by ~1.0 °C
  at the same hours. The inversion index is a *difference* between the two, so
  biases that move together largely cancel: the index is far less biased than
  either input. Reading the two findings together is what makes them useful —
  either alone would have pointed the wrong way.

  Caveats, stated because they matter: the two legs are 15 km and different
  years apart (the valley check used 2024, the ridge 2026); the ridge point is
  a grid cell against a point station on complex terrain; and the coordinates
  are inferred, not published.

* **There is no observed snow record for Florina, from anywhere.** Four
  independent sources, all checked and all negative: GHCN-Daily carries the
  station as `GRE00105242` with `TMAX, TMIN` only; data.gov.gr's 60-field
  station files have no snow column; EMY's own AUTO reports
  (`613 071200Z AUTO … 24/01 Q1021`) have no snow group; and NOAA ISD's
  additional-data section is precipitation-only. The nearest long snow record
  is Makedonia, 160 km away at 6.7 m — a coastal airport, useless for a 662 m
  inland basin.

  So snow is **proxied**: a day counts when precipitation was recorded and the
  minimum was at or below 1 °C. That definition is loose on purpose, so two
  different observing networks can be compared without an instrument change
  standing in for a climate change. Two checks say it is fair: ISD and NOA
  agree on daily minimum to **−0.09 °C over 3 334 overlapping days**, and
  ERA5's independent snow-day count for 2010–2023 (**26.1**) lands within two
  days of NOA's (**24.1**).

  `research/snow_climatology.py` derives the shipped tables and prints them, so
  the constants in `report.py` have provenance rather than being magic. It
  caches ~7 MB under `research/_cache/`, which is ignored.

* **The finding, and the version of it that is actually true.** Snow days per
  winter in Florina roughly halved:

  | | snow days/winter | January, share of days |
  |---|---|---|
  | ISD, 1932–1981 | **60** | 64 % |
  | NOA, 2010–2023 | **24** | 22 % |

  ERA5 shows the same shape across the years the gauges miss — 46 days in the
  1940s to 21 in the 2020s.

  The tempting headline is "less snow". The accurate one is **fewer snowfalls,
  not smaller ones**: totals fell by half (117 → 54 cm) but the *deepest single
  day* per decade did not, and the 2020s are the highest of any decade at
  32 cm. The season is not producing weaker storms; it is producing far fewer
  opportunities. That distinction is why someone can remember 2023–24 as a real
  winter — 32 cm in one day, the largest since 1983 — while the day count was
  only 13.

  Caveats kept in view: the old-era absolute level differs between ISD (60) and
  ERA5 (40), ISD covers 24 of 50 winters, and gauges undercatch snow — which
  depresses both eras equally and so largely cancels from the comparison, the
  part that matters.

* **Headless Chrome reports `prefers-reduced-transparency: reduce` by default.**
  Any screenshot taken through CDP is therefore the *no-blur* variant unless the
  preference is explicitly emulated back to `no-preference`. It is a real
  supported feature, not an unknown one, so the fallback block genuinely
  engages — worth knowing before concluding the blur "doesn't work".
* **The old EMY scraper is gone, but `oldportal.emy.gr` is not dead.**
  previous `extract_emy.py` / `emy_extract.py` scrapers were retired and then
  deleted: they pulled `meteoalarmJson` out of the EMY warning page with a
  regex, and warnings now come straight from the official Meteoalarm CAP feed,
  which is the same upstream data EMY was republishing and needs no scraping at
  all. The CAP shape they documented lives on in
  [`tests/fixtures.py`](../tests/fixtures.py).

  **Correction:** the note here used to say the host no longer responds. It
  does — the meteogram and climatology pages both answer 200, checked directly.
  The scraping was retired because the CAP feed is better, not because the site
  died, and saying otherwise was wrong. The HTML page dumps those scripts
  produced were removed from the repository all the same: that markup was EMY's,
  not this project's, so it did not belong under its MIT licence.
* **A real observation is available after all, via data.gov.gr.** The portal
  publishes the EMY automatic station network — 93 stations — and it runs on
  CKAN, whose datastore API answers **without a token**. That is what the
  station card uses. Two things worth recording:

  * **There is no station in Florina.** Station 230 is ΒΙΤΣΙ, the mountain
    above the town, at ~1700 m. Its 823 hPa barometer gives it away, and the
    package title says so outright. The nearest station that is actually kept
    fresh is **007, Καστοριά**, 30 km west in the next basin, whose 947 hPa is
    right for its 620 m.
  * **Freshness varies wildly by station.** Kastoria and Ioannina run about
    5½ hours behind; Vitsi and Grevena about 21; Chortiatis was a month stale.
    So the card shows the observation time and the age rather than implying the
    number is current, and hides itself entirely past `station_max_age`.
  * Missing readings are a **run of slashes of varying length** — `/`, `///`,
    `/////` all mean "no sensor". Parsing those as 0 would invent a frost or a
    dead calm, which is why `_reading` rejects them explicitly.
