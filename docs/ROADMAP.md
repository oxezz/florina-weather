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

**A frost alert webhook** — push a message when the frost card fires, via ntfy
or Telegram, using `urllib` so the app keeps its no-dependencies rule. Needs a
decision first: which service, and the topic name or bot token. Nothing else
about it is hard.

**A real station reading next to the forecast.** Not yet shown to exist.
Checked so far:

- **aqicn** — `feed/florina` returns `Unknown station`, but the demo token
  ignores the search term entirely and serves a fixed dataset (a Greece search
  came back with Bangalore), so the demo cannot settle it. A real token is
  needed before drawing any conclusion.
- **NOA / meteo.gr** — the station search requires a login, and the open-data
  URL 404s.
- **sensoto** — surfaced in search for Florina, but serves an app shell with no
  reachable endpoint.

So the honest position is unresolved rather than absent. A station in the basin
would be genuinely valuable: it would let the whole model stack — the inversion
index especially — be checked against reality instead of against itself.
