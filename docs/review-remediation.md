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

## 2026-10-06 — NBA in, MLB out of the digest

The owner swapped the digest's sports from NFL/MLB/UFC to NFL/NBA/UFC, as
planned for NBA opening night, effective from the next scheduler restart
(MLB postseason picks stop being emailed now; NBA picks begin when the nba
season window opens on 10-20, so the gap is NFL and UFC only).

Measured first (`backend/scripts/nba_market_experiment.py`, 2025-26, last
30% out of sample, 376 games): the NBA model adds nothing beyond the closing
spread (+0.16, 95% CI -0.19..+0.52, p 0.37); Brier model 0.1696 vs market
0.1594; betting its disagreements 188-186. The owner chose to publish NBA
anyway, consistent with NFL, which also measured no edge
(`epa_experiment`). MLB picks are still generated and graded; only the email
changes.

## 2026-10-10 — price window off; confidence level shown; college football in

**Owner decision.** "Remove the filter on the email notification and send
the best available but include a confidence level", with NCAA football
added to the digest sports.

- **Price window off.** `digest.send_bar.min_odds` / `max_odds` set to
  -100000 / +100000 in `config.yaml` (was -150 / +150). Picks are still
  ranked by raw edge and capped (`max_game_picks: 3`, `max_props: 5`); a pick
  with no stored price is still dropped. Tracking-only picks (spreads,
  totals, MMA, football props) stay out -- `PickModel.published()` is
  unchanged.
- **Confidence level.** Every emailed pick shows its stored tier in words:
  tiers 4-5 "High", 3 "Medium", 1-2 "Low". Stars stay hidden. Measured the
  same day on all graded published moneylines: the tier has NOT tracked the
  win rate (tier 1: 35% of 197; tier 4: 43% of 67; tier 5: 45% of 29, model
  average 61%). NCAA football moneylines: 31-60, against a model average
  near 58%. The label is the model's own confidence, not a measured one.
- **Started games left out.** The digest now skips any game whose start
  time has passed (a late send carries only what can still be bet).
- **ncaaf** added to `digest.sports`.

## 2026-10-10 (later) — caps per sport: all NFL picks, college football 10, props 10

**Owner decision.** "I want to see picks on all NFL games. NCAA can be capped
at 10. Props can be capped at 10 too."

- `digest.send_bar.sport_caps` (new, optional) overrides `max_game_picks` /
  `max_props` per sport; `null` means no cap. Set to `nfl: {max_game_picks:
  null}`, `ncaaf: {max_game_picks: 10}`; `max_props` (the default) 10; other
  sports keep `max_game_picks: 3`. A malformed entry raises, as the rest of
  the send bar does.
- "All NFL games" here means every NFL game that HAS a published pick. A
  pick is generated only on 3+ points of edge (strategy `min_edge`), so on
  recent Sundays 5, 11 and 7 of 14 games had one (2026-09-20, 09-27, 10-04).
  Covering every game would need picks below that floor -- a separate
  decision, not made here.

## 2026-10-10 (later) — a published pick on every NFL game

**Owner decision.** "Publish picks on every NFL game."

- `ensemble.EVERY_GAME_SPORTS = ("nfl",)`: when neither moneyline side clears
  `min_edge` (3) and `max_edge` (20), the side with the larger edge over
  break-even is published anyway, at confidence 1 ("Low") whatever its edge,
  with its real edge -- which can be negative. A ceiling-refused edge (>= 20,
  a likely model error) is therefore published as Low, not High.
- Measured on a snapshot for 2026-10-11: 13 of 13 NFL games get a published
  moneyline (7 cleared the bar as before; 6 are every-game picks, edges -0.7
  .. +21.1). Before, recent Sundays had 5, 11 and 7 of 14.
- Sizing is unchanged: the generator re-sizes every pick by Kelly, so a
  negative-edge pick sizes 0 units, but a ceiling-refused one keeps a Kelly
  size (2.64 units on the 10-11 snapshot). Sizes are suggestions; no bet is
  placed automatically.
- Spreads and totals stay tracking-only.
