# archive

Obsolete material, kept for reference only. Nothing here is imported or served
by the application.

| File | What it was |
|------|-------------|
| `extract_emy.py`, `emy_extract.py` | Two near-duplicate scrapers for `oldportal.emy.gr`. They pulled `meteoalarmJson` out of the EMY warning page with a regex, plus a UV table and a meteogram. |
| `meteoalarm.html`, `emy_raw.html` | Saved copies of those pages, from when the portal still answered. |

They were retired in the 2.0 rewrite because **`oldportal.emy.gr` no longer
responds** — both scripts fail at the first request. Warnings now come straight
from the official Meteoalarm CAP feed (`feeds.meteoalarm.org`), which is the
same upstream data EMY was republishing, and which needs no HTML scraping.

The saved HTML is still occasionally useful: it shows the exact CAP JSON shape
EMY produced, which is what `tests/fixtures.py` is modelled on.
