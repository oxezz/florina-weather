# archive

Obsolete material, kept for reference only. Nothing here is imported, tested or
served by the application.

| File | What it was |
|------|-------------|
| `extract_emy.py`, `emy_extract.py` | Two near-duplicate scrapers for `oldportal.emy.gr`. They pulled `meteoalarmJson` out of the EMY warning page with a regex, plus a UV table and a meteogram. |

These were retired in the 2.0 rewrite because **`oldportal.emy.gr` no longer
responds** — both scripts fail at the first request. Warnings now come straight
from the official Meteoalarm CAP feed (`feeds.meteoalarm.org`), which is the same
upstream data EMY was republishing, and which needs no HTML scraping at all.

The saved page dumps these scripts produced (`meteoalarm.html`, `emy_raw.html`,
about 250KB of EMY's own markup) were **removed from the repository**. They were
not this project's work, so they had no business being covered by its MIT
licence. The CAP JSON shape they documented lives on in
[`tests/fixtures.py`](../tests/fixtures.py).
