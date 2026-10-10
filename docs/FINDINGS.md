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

## 2026-10-10 — UFC history import, measured on a snapshot

CSV refresh: github.com/Greco1899/scrape_ufc_stats, downloaded 2026-10-10
(results 8,950 rows: W/L 5,625, L/W 3,167, D/D 66, NC/NC 92; events 791 rows,
latest UFC 332 on 2026-10-03). Import run with `--apply` on a `sqlite3.backup`
snapshot of the live db only.

- Parsed 8,833 bouts, 1994-03-11 .. 2026-10-03. Skipped: 92 no-contests, 25
  bouts whose event name carries two different dates.
- Inserted 8,749 final MMA games; 69 skipped as already stored final (same
  two fighters by name key within 2 days -- the odds-feed and March-July
  rows); 2,509 new fighter rows; 0 ambiguous existing names. Near-duplicate
  bouts (same pair, <= 1 day apart): 0 before, 0 after.
- 15 bouts matched an existing game that is NOT final -- 12 stuck
  `scheduled` and 3 marked `canceled` from March-July 2026 (e.g. Chimaev v
  Strickland 05-10, Pereira v Gane 06-15, McGregor v Holloway 07-12), none
  with a pick or a paper bet. The importer leaves them as they are (it never
  modifies existing games) and lists their ids; their results are therefore
  NOT in the Elo replay. Owner, 2026-10-10: finalize them from the CSV
  (`--finalize-unfinished`, only games with no pick or paper bet) -- all 15
  qualified; the replay then covers 8,867 bouts.
- Owner, 2026-10-10, on the underdog finding below: keep every MMA pick
  tracking-only until the combat model is recalibrated.
- MMA Elo replayed over 8,852 bouts. Top of the table is recognisable: Jon
  Jones 1715.8, Islam Makhachev, Georges St-Pierre, Charles Oliveira, Khabib
  Nurmagomedov, Amanda Nunes, Aljamain Sterling, Max Holloway.
- Coverage of the next 30 days' MMA cards (37 bouts, 74 fighters): fighters
  with history 6 -> 46; bouts with history on both sides 0 -> 20. Of the 32
  still unmatched at first, 4 were one fighter under another spelling (now
  aliased: Alex/Alexander Volkanovski, Lupita/Loopy Godinez, Su Young/SuYoung
  You, Timothy/Timmy Cuamba); the rest fight outside the UFC (several KSW
  cards), so the new gate gives them no pick.

**Finding -- not fixed, owner's call.** Regenerating the 10-10, 10-17 and
10-24 cards on the snapshot gave 15 picks, none at 0.5, but **all 15 are
underdogs** (mean price +193): mean model probability 0.481 against the
market's no-vig 0.338. The cause is structural, not sampling: with K=24 the
MMA ratings barely spread (sd 31.6 points, 5th-95th percentile range 98.6),
so the model cannot rate any fighter much beyond ~64% while the market prices
70-80% favourites routinely -- every favourite looks overpriced, every dog
like value. History removes the no-information 0.5, not this bias. Options:
keep MMA picks tracking-only until the combat model is calibrated; recalibrate
(measure leak-free pre-fight Elo against outcomes over the 8,749 bouts, tune
K and the probability scale); or blend toward the market price.

Known limitation: the CSV has names, not fighter ids, so namesakes (e.g. two
"Bruno Silva"s) share one record. Not checked: the calibration of the combat
model on history (above), boxing, and the effect on past MMA picks (their
stored probabilities are unchanged).
