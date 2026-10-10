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

## 2026-10-10 — MMA recalibration (fitted on UFC history)

Method (backend/analysis/combat_history.py, combat_calibration.py): leak-free
replay of all 8,867 final MMA bouts in date order -- each bout's features are
what the live pick generator would have seen before it (pinned by a test
against `_build_fighter_stats`); logistic model on difference features
(elo_diff/400, form_diff, quality_diff/400, log1p fight-count diff), no
intercept, fitted on every bout as stored AND mirrored, because UFCStats
lists winners first (first-listed won 64% of imported bouts). Fit < 2021,
choose K on 2021-2023, report 2024+ once. Only bouts the live model prices
(both fighters with history, decided).

- Validation 2021-2023 (n=1,221), log-loss by K: 16 .6819, 24 .6823, 32 .6825,
  48/64/96 .6826 -- K barely matters; chose 16.
- Coefficients (refit < 2024): elo 2.5169, form 0.7308, quality 2.3862,
  experience 0.0340.
- Test 2024+ (n=1,181, the same bouts for both models): log-loss .6747, Brier
  .2409 vs the old blend at K=24 .6815 / .2442. Coin flip: .6931 / .25.
  Better than the blend, but fight outcomes are only weakly predictable from
  these features (mean |p - 0.5| = .056). No interval was computed (a
  card-clustered bootstrap would give one); treat the gap as modest.
- K=16 is the smallest K tried: validation fell monotonically toward it, so
  the best K may be lower. The spread (.6819 to .6827) is noise-level.
- **Against the market**, PRE-FIGHT prices only (each book's last line
  snapshot captured before 00:00 UTC on the bout date; the `odds` table was
  NOT used -- 175 of its 593 MMA rows were written on or after the fight date
  and 21 carry |moneyline| >= 1000, i.e. in-play or settled prices): n=66
  bouts with history on both sides, about a dozen cards, so correlated --
  this cannot show an edge. Model log-loss .6658, Brier .2365; **market .5896
  / .2012**. The books know far more. At the live 3% edge bar the model would
  bet 53 of 66, **96% of them underdogs** (mean model .476 vs market .312).
- The replay takes every bout's features before any result on that date
  applies (live reads only earlier dates); early UFC tournaments put a fighter
  in several bouts one night, listed final first. Fixing this moved the
  coefficients by under 0.02.
- Snapshot after replaying Elo at K=16: MMA Elo sd 31.7 -> 21.6; top ten still
  the champions (Jones, St-Pierre, Makhachev, Holloway, Oliveira, Khabib,
  Nunes, Sterling, D. Johnson, Volkanovski). The next three cards: 15 picks,
  all underdogs, all tracking-only; model .313-.575 (sd .064), mean .462 vs
  market .338.

**Conclusion.** The recalibrated model is honest about outcomes and better than
the old blend, but it is not competitive with the market, so any edge bar
still selects almost only underdogs. Recommendation (owner's call): keep MMA
tracking-only. To publish MMA ever, the model needs information the market
lacks (it is unlikely to come from Elo/form/opponent quality alone), or the
pick rule must be anchored to the market (shrink the model toward the no-vig
price, as the NFL/MLB shrink weight does) -- which on this evidence would leave
almost no MMA picks.

## 2026-10-10 — Boxing history from Wikipedia

Source: English Wikipedia "Professional boxing record" tables, read through
the MediaWiki API (`backend/collectors/wikipedia_boxing.py`), cached on disk
(2,410+ responses, 2026-10-10). Wikipedia text is CC BY-SA 4.0; we store only
the facts of each bout (date, the two fighters, the result), never page text,
and name the source here and in `docs/data-dictionary.md`. Import run with
`--apply --finalize-unfinished` on a `sqlite3.backup` snapshot of the live db
only (run 6, the code as merged).

- Fighters to look up (every boxer on a stored card since 2026-01-01): 336.
  Own page found 178; missing 158. Opponent pages read: 696. Rows skipped,
  never guessed: 214 no result (no-contests, bouts listed before they
  happen), 141 bad date, 27 no opponent.
- Inserted 23,235 final boxing games, 1973-06-25 .. 2026-10-09 (2,295 already
  stored as the same pair within 2 days, 57 of them the same bout under
  another spelling of one fighter's name; 13,854 new fighter rows; 0
  ambiguous). Near-duplicates: same pair within 2 days 0; one fighter
  against two opponents whose names share a word, within 2 days, 0 (112
  pairs with unrelated names remain: tournament nights such as Prizefighter,
  and late substitutions such as Abdullah Mason v Cordina -> Bell).
- 78 boxing games stuck `scheduled`/`canceled` from March-October 2026, with
  no pick and no paper bet, finalized from the records (owner rule, as for
  UFC). 11 more matched a non-final game that HAS a pick or paper bet and are
  left as they are (ids 1313-1317, 1452, 1535, 1538, 1540, 1611-1615).
- Boxing Elo replayed from seed over 23,313 bouts. Top: Canelo Álvarez 1931,
  Petch Sor Chitpattana, Floyd Mayweather Jr., Naoya Inoue, Gilberto Ramirez,
  Oleydong Sithsamerchai, Wladimir Klitschko, Terence Crawford, Jaime
  Munguia, Tyson Fury. (The two Thai fighters have long records against weak
  opposition; Elo cannot tell that from quality.)
- Coverage of the stored upcoming cards (24 bouts, 48 fighters): fighters
  with history 0 -> 33; bouts with history on both sides 0 -> 13.

**What the earlier runs got wrong (fixed before the merge, each with a test
that fails without the fix):** ISO dates and `{{small|...}}`-wrapped
dates were unread (3,545 rows skipped); the search fallback accepted any page
with a record table, so 106 of 281 "found" fighters had someone else's record
(Jordan Orozco had Terence Crawford's, Jason Limon had Valentina
Shevchenko's) -- a page must now name our fighter in its title or bold lead
name; Floyd Mayweather Jr. and Sr. were one fighter -- boxing names keep
Jr./Sr./II-IV (`ufcstats_history.fighter_key`); five results with typo years
(2105..2916) were stored final and replayed last -- a result dated after
today is a bad date; 82 record sections lost their table because the page
closes it with `{{s-end}}`, not `|}` -- 15 recovered, the other 67 have no
table in the section; and (found by the final review) one bout reached
through both fighters' pages under two spellings of a name ("T. J. Doheny"
/ "TJ Doheny", "Christopher Gurrero" / "Guerrero") was stored twice -- about
52 bouts -- and 9 feed games stayed stuck beside a final twin (the feed's
"Jermaine Franklin Jr" v Moses Itauma): a boxing bout whose fighter already
has a bout within 2 days against an opponent whose name shares a word is
now the same bout.

**Why fighters are missing (five checked by hand, from the cache):** Jordan
Orozco, Kayla Allen -- no article of their own; Nathan Heaney -- an article
with no record table; Jermaine Franklin Jr -- the page is "Jermaine Franklin"
and boxing keys keep the suffix; Pat Brown -- the page is "Patrick Brown
(boxer)". The last two are the safe way to be wrong (no history rather than
someone else's).

**Finding -- the underdog bias carries over to boxing.** Regenerating the
10-10 and 10-17 cards on the snapshot gave 5 picks, all tracking-only, **all
5 underdogs** (+259 .. +1762): mean model probability 0.42 against the
market's no-vig 0.18. n=5 from two cards, so it is a direction, not a size --
but it is the MMA result again (compressed ratings make every favourite look
overpriced). Three of the five claim more than 20 points of edge: the combat
model has a minimum edge and no ceiling (`ensemble`'s 20-point ceiling does
not apply). Boxing picks are tracking-only from this merge
(`TRACKING_ONLY_SPORTS`), so nothing is published or emailed.

Known limitations: names, not ids -- two boxers sharing a name share a row; a
record table edited after 2026-10-10 is not re-read (the cache is never
refetched).
