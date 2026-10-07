# Roadmap

Work that is understood but not built. Each entry says what the blocker is, so
it can be picked up without re-deriving the reasoning.

> These belong in GitHub Issues. They live here because creating issues needs a
> token this project does not have; the headings below are written so each one
> can be pasted straight into an issue.

**A rain-radar layer, waiting on rain.** A [RainViewer](https://www.rainviewer.com)
overlay was considered and is parked until it can actually be checked. The
blocker is not the map, it is the data:

- RainViewer returned **13 past frames and 0 nowcast frames**, so it shows where
  rain is, never where it is going.
- **Greek coverage is unverified.** At z=5 the tile covering Florina is 2496
  bytes against Athens' 531, so it holds *something* — but that tile spans
  longitude 11.25°–22.5°, so the content may be rain over Italy. On a dry day
  there is no way to tell "no rain over Greece" from "no radar over Greece".
  Shipping it blind risks a blank rectangle that looks like a working feature.

**How to check it, on the first wet day.** Pick a city where Open-Meteo
forecasts rain, fetch its RainViewer tile, and compare against Florina's. If the
wet city's tile is large and Florina's is still ~300 bytes during forecast rain,
there is no Greek coverage and the idea is dead.

**If it does check out**, the lightweight shape is one regional tile served
*through our own server* as a plain `<img>`, with the town marked by a CSS dot at
a computed pixel offset. No Leaflet (144 KB — 2.4× this whole app), no CSP
change, no third-party requests from the browser, cached like every other
source, and roughly 2.5 KB dry. The 13-frame loop would be about 30 KB.

**A frost alert webhook** — built, not merely planned. Both ntfy and Telegram
are implemented in `notify.py`, selected by `FLORINA_ALERT_WEBHOOK`; see the
README's *Frost alerts* section. Nothing is sent until a transport is chosen
and given somewhere to send to, which is the only remaining step and is a
configuration change rather than code.

**Correct the inversion index for its night bias.** Measured, not suspected: the
archived forecast runs **1.6 °C too warm at night** in the valley against
Florina's own observations (WMO 16613, 472 paired hours, January and July 2024),
which makes the index **understate** inversions. See
[`DESIGN.md`](DESIGN.md) for the table.

Not applied, deliberately. A correction on a one-year, two-month sample would be
a fudge dressed as calibration, and the archive grid is 728 m against the
station's 662 m, so the honest bias is nearer 2 °C, not smaller. What it needs
before anyone should trust a number:

1. **Several years**, not two months, from ISD-Lite — the files are there for
   2024 and 2025 but 2026 is not yet published.
2. **Both legs of the comparison.** Only the valley leg is checkable today.
   Vitsi is the obvious slope station and its barometer puts it near 1800 m, but
   it publishes no coordinates and no WMO number, so its position is inference.
   EMY's own open-data register lists station coordinates as available on
   request — that document would settle it.

**A real station reading next to the forecast.** Built, with a correction. There
is no EMY station **in the open-data portal** for Florina, but there is one in
the town: **WMO 16613**, 40.783 N, 21.400 E, 662 m. The card shows Καστοριά
(007) and Βίτσι (230) as references because those are what data.gov.gr
publishes, and it says so on the page rather than implying they are the town's.
