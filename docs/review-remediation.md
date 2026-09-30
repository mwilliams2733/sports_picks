# Review remediation

Dated notes on decisions made in response to the outside review of the
digest/picks pipeline. Each section is append-only: a later decision gets a
new dated section rather than an edit to an old one.

## 2026-09-29 — frozen send bar; stars hidden

**What shipped.** The daily digest no longer emails a game pick by
confidence-star ranking. It now emails only picks that clear a frozen "send
bar" in `config.yaml` under `digest.send_bar`:

```yaml
digest:
  send_bar:
    min_shrunk_edge_pp: 3.0
    min_odds: -150
    max_odds: 150
    max_game_picks: 3
    max_props: 5
    blend_weight:
      nfl: 0.00
      mlb: 0.00
```

- `min_shrunk_edge_pp` — a game pick must have shrunk edge (below) at least
  this many percentage points.
- `min_odds` / `max_odds` — the American price window, inclusive at both
  ends. A pick with no stored price is dropped (previously it was kept and
  shown at -110).
- `max_game_picks` / `max_props` — caps per sport per day.
- `blend_weight` — λ per sport. **Shrunk edge = λ[sport] × edge_pct**, where
  `edge_pct` is the stored `PickModel.edge_pct` (model minus market, in
  percentage points; de-vigged for moneyline, measured against 0.5 for
  spreads/totals — see `backend/analysis/variants/ensemble.py`). A sport
  missing from `blend_weight` counts as λ = 0, which means it never clears
  the bar (shrunk edge is always 0 while `min_shrunk_edge_pp` is 3.0) until
  it is added with a nonzero weight.

**Why λ = 0 for both measured sports.** λ was measured on 2026-09-29 with:

```
python -m backend.analysis.market_shrinkage --db <snapshot> --sport <s>
```

| Sport | Games | Best λ | Brier: market alone → model alone |
|---|---|---|---|
| NFL | 1,184 | 0.00 | 0.2109 → 0.2258 |
| MLB | 127 | 0.00 | 0.2216 → 0.2438 |

Brier rose monotonically with λ in both sports — i.e. blending any amount of
model opinion into the market price made the combined forecast worse,
in-sample, for both sports measured. The best in-sample λ is 0, so the send
bar currently blends none of the model's opinion in: only the market's
own price informs `shrunk_edge`, which means (with λ = 0) no game pick can
ever clear `min_shrunk_edge_pp` > 0. This is intentional: the bar is closed
until there is evidence the model adds anything beyond what the market
price already says.

**Re-enable condition.** The bar starts passing picks for a sport once that
sport's **held-out** λ (not in-sample) is measured above 0 — i.e. blending
some nonzero weight of the model's opinion with the market price improves
Brier score on data the λ search did not touch. Until then, `blend_weight`
for that sport stays at 0.00.

**Change control.** These values are frozen as of the owner decision on
2026-09-28 (implemented 2026-09-29). Any change to `send_bar` (including
`blend_weight`) requires a new dated section in this file explaining the
measurement and the reasoning, the same way this section does. Do not
change `config.yaml`'s `digest.send_bar` block without adding one.

**Stars hidden.** Confidence stars (`★`/`☆`) are removed from the daily
email (both the HTML and the plain-text alternative) and hidden from the
frontend behind a single switch, `SHOW_STARS` in
`frontend/src/lib/display.ts`, currently `false`. The underlying
`confidence` field and its data are not deleted anywhere — only the star
rendering is suppressed. This includes the Track Record confidence
breakdown table, the Player Props and Today's Picks confidence columns and
the Player Props minimum-confidence filter, and the FAQ's confidence-star
explainer.
