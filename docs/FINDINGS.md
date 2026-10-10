# Findings

Dated measurements and investigations. `CLAUDE.md` keeps live invariants and
links here; superseded findings stay, marked as such.

## 2026-10-09 — MMA picks at model_prob 0.5 (no information)

Read-only investigation (owner decision (c), with the stopgap (b) shipped on
the same branch: a pick at exactly 0.5 is stored tracking-only).

**Cause.** In 60 of 61 published MMA picks since 2026-09-01, *neither* fighter
had any earlier bout in our database, so both fall back to identical defaults
and the combat model returns exactly 0.5:

- `backend/pipeline/pick_generator.py` (`_build_fighter_stats`, ~line 600 on
  this branch): no `EloRating` row -> `elo_rating = 1500.0`; no earlier final
  bout -> `recent_form_score=0.5`, `opponent_avg_elo=None`, `fights_count=0`.
- `backend/analysis/variants/combat_sports.py:83-98`: equal Elo -> elo term
  0.5; equal form -> form term 0.5; no opponent quality -> fallback
  `0.78*elo + 0.22*form` = 0.5.
- `combat_sports.py:34-41`: boxing returns no pick when either fighter has
  `fights_count == 0`; MMA deliberately skips that gate on the premise that
  "UFCStats coverage is comprehensive enough that fights_count=0 is a real
  debut signal". That premise is false today: these fighters are not
  debuting, we simply hold no history for them.
- Effect: 0.5 against any underdog price is a large "edge". All 60 were
  underdogs (positive odds, mean +263) -- hence the 19% average edge. The
  published MMA record since September is, in effect, "always back the dog".

**Counts** (picks since 2026-09-01, not withdrawn, not props):

| sport | published n | at 0.5 | tracking n | at 0.5 |
|---|---|---|---|---|
| mma | 61 | 60 | 0 | - |
| mlb | 128 | 0 | 19 | 0 |
| ncaaf | 176 | 0 | 67 | 0 |
| nfl | 25 | 0 | 19 | 0 |

MMA by game month: 2026-03, 26 picks, all model_prob NULL (an older path, not
checked); 2026-09, 37 picks, 37 at 0.5; 2026-10 (to 10-09), 25 picks, 24 at
0.5 (one later withdrawn). The one exception (pick 8280, 0.4557) had one prior
bout for one fighter. Record of the 0.5 picks: 16-29, n=45 graded, +22.26
summed payout -- far too small to judge either way.

**Why there is no history.** Not a naming problem: normalising all 403 MMA
team names (accents, punctuation, Jr/Sr) found no duplicate groups. The 50
earlier MMA finals (March-July, apparently a UFCStats scrape, team ids
65-641) share no fighters with the current cards; August has no MMA rows;
since 09-22 every MMA game comes from Odds API events, creating new team rows
(~845-1233). `elo_history` for MMA starts in 2026-09 (206 rows, written after
each bout by `grader._apply_combat_elo_update`). Nothing back-fills fight
history: `scheduler.ingest_recent_ufc_event` (scheduler.py ~982) has no
caller (dead wiring), and `backend/scripts/backfill_ufc_elo.py` (a one-shot
CSV replay) shows no sign of having been run. Fighters fight 2-3 times a
year, so without a backfill nearly every bout stays at 0.5 into mid-2027.

**Options** (no fix made; owner's call):
1. Gate MMA like boxing: no pick when either fighter has `fights_count == 0`.
   One line and a test. MMA picks drop to almost none for months -- honest.
2. Backfill history: run `backfill_ufc_elo.py` on a historical UFC dataset
   and/or wire UFCStats event ingestion, fill the Aug-Sep gap, then replay
   combat Elo from seed (`dedupe_combat_games.rebuild_combat_elo`). Moderate
   cost (data source, name matching to Odds API names, scheduler stop).
   Risk: unmatched names silently fall back to 0.5 -- measure the match rate.
3. Interim (what the stopgap already does): 0.5 picks are tracking-only, off
   the board and the email, while option 2 is built.

**Not checked:** where the March NULL-probability picks came from; that
`_build_fighter_stats` reads the *current* `EloRating` with no date filter
(fine live, but it leaks future results into any backtest); whether a usable
UFC dataset or the UFCStats scrape still exists; boxing (no picks in the
window); paper-trading exposure to these picks; whether a pick is re-scored
after its fighters are graded.
