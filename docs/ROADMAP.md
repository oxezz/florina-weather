# Roadmap

Work that is understood but not built. Each entry says what the blocker is, so
it can be picked up without re-deriving the reasoning.

> These belong in GitHub Issues. They live here because creating issues needs a
> token this project does not have; the headings below are written so each one
> can be pasted straight into an issue.

**A frost alert webhook** — built and verified end to end against a real
HTTP sink, not just against an injected opener: delivery, the Greek title
surviving the wire, suppression inside the window, and an escalation getting
through regardless. Nothing left but the environment variables. Both ntfy and Telegram
are implemented in `notify.py`, selected by `FLORINA_ALERT_WEBHOOK`; see the
README's *Frost alerts* section. Nothing is sent until a transport is chosen
and given somewhere to send to, which is the only remaining step and is a
configuration change rather than code.

**Correct the inversion index for its night bias.** Done, with a caveat that
matters more than the number.

The blocker named here was sample size — an earlier check rested on 472 hours
of one year. The archived-forecast API reaches back to 2017, so the valley leg
is now measured over **21 837 paired hours across nine years**:
python research/forecast_bias.py.

| | bias | n |
|---|---|---|
| night (21:00-06:00) | **-1.65 C** | 9 824 |
| cold-month night | -1.25 C | 3 423 |
| day (09:00-18:00) | **+0.02 C** | 9 720 |

The forecast runs warm at night and is unbiased by day, which is what makes it
a night effect rather than a calibration error. The ridge leg is weaker — Vitsi
publishes no coordinates and its record covers one warm season — and measures
about -1.33 C over the same nights. The valley being the more wrong of the two
understates the difference by about **0.73 C**, which 
eport.py now applies.

The caveat: 0.73 carries an uncertainty near 0.4, so where the correction moves
a reading across a band edge the card says **«Πιθανή αναστροφή»** rather than
claiming a definite inversion. Without that the correction would silently drop
the threshold from 1.5 to about 0.8 and every marginal night would read as
fact. The raw valley temperature is exposed alongside the corrected one.

Still open, and now the only thing left: **Vitsi's coordinates.** EMY lists
them as available on request. That would turn the ridge leg from an inference
into a measurement and let the correction shrink toward its own uncertainty.

**A real station reading next to the forecast.** Built, with a correction. There
is no EMY station **in the open-data portal** for Florina, but there is one in
the town: **WMO 16613**, 40.783 N, 21.400 E, 662 m. The card shows Καστοριά
(007) and Βίτσι (230) as references because those are what data.gov.gr
publishes, and it says so on the page rather than implying they are the town's.
