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

| Sport | Games | Date span | Best λ | Brier: market alone → model alone |
|---|---|---|---|---|
| NFL | 1,184 | 2022-09-08 .. 2026-09-28 | 0.00 | 0.2109 → 0.2258 |
| MLB | 127 | 2026-05-24 .. 2026-09-27 | 0.00 | 0.2216 → 0.2438 |

Both were measured against a `sqlite3 .backup` snapshot of the live db taken
2026-09-29 (a live-connection copy risks a WAL-torn read; see
`sports-picks-db-snapshot` in project memory).

Brier rose monotonically with λ in both sports — i.e. blending any amount of
model opinion into the market price made the combined forecast worse,
in-sample, for both sports measured. The best in-sample λ is 0, so
`blend_weight` is 0.00 for both, which makes `shrunk_edge` (= λ × edge_pct)
exactly 0 for every pick in both sports, regardless of that pick's price or
edge — price is not a separate input to `shrunk_edge`; it enters only
through `edge_pct`, and λ = 0 zeroes it out entirely. With
`min_shrunk_edge_pp` at 3.0, that means no game pick in either sport can
ever clear the bar. This is intentional: the bar is closed until there is
evidence the model adds anything beyond what the market price already says.

**This is an in-sample bound, not a held-out result.** An in-sample best λ
of 0 means blending hurt on the SAME data the λ search was fit to — it does
not by itself say anything about held-out performance, because a held-out
search could in principle prefer a different λ than the in-sample one did.
What it does bound is the ceiling: if blending the model in already looks
worse on the data most favorable to it (the data it was evaluated on), it
is very unlikely to look better on unseen data. Realistically, re-enabling
either sport needs more than a re-run of this same search on fresher data —
it needs a changed model or new features that give the model something the
market price does not already have, followed by a fresh **held-out**
measurement (below).

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

## 2026-09-30 — 3-point shrunk-edge bar lifted; raw edge shown

The owner lifted the 3-point shrunk-edge bar to see raw edges and judge for
themselves; picks remain tracked in full for analysis. The brief's non-goal
on raw edges is knowingly set aside by the owner.

`min_shrunk_edge_pp` is removed from `config.yaml`'s `digest.send_bar` and
from the selector's required keys. `blend_weight` stays in config as the
recorded 2026-09-29 market-shrinkage measurement (see the section above),
but it no longer filters or ranks picks -- in this code, every priced game
pick (price in [min_odds, max_odds], same as before) is emailed, ranked by
raw `edge_pct` descending, up to `max_game_picks`. **This is not yet live**:
as of this section, the change exists only on branch `feat/edge-and-export`
and has not been merged to `master` or deployed. "Every priced game pick is
emailed" becomes true starting with the first digest run after this branch
merges and the scheduler restarts -- provisionally the morning of
2026-10-01 if merged promptly, but that date is not yet a fact, only a
projection; see `docs/data-dictionary.md`'s "known traps" for the same
caveat stated against the data itself. The email shows each game pick's
edge (e.g. "Edge +6.2 pts") and a footer line explaining what it means and
that it is not a validated signal. Props still show no edge -- prop
`edge_pct` is a stat-unit gap, not a probability difference, and remains
not comparable to a game pick's edge (see the "known traps" section of
`docs/data-dictionary.md`).

A leftover `min_shrunk_edge_pp` key in `send_bar` is ignored with a WARNING
log rather than raising, since the key is now inert either way and raising
would turn a harmless leftover into a full digest outage.

**Pick export, for offline analysis.** `python -m backend.scripts.export_picks
--db <path> --out <dir> [--since YYYY-MM-DD] [--sport nfl ...]` writes a
read-only, complete CSV export of every stored pick (`picks.csv`), the
append-only price history (`line_history.csv`), and a manifest, for a data
scientist working outside this repo. Paper bets and user data are excluded.
See `docs/data-dictionary.md` for column definitions, grading and CLV
conventions, and known traps in this data (repo has no root README; this
file is the closest thing to a docs index, hence the pointer here).
