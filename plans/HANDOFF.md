# Session handoff — 2026-09-17

Everything below is recoverable from `git log` and `plans/`; nothing important
lives only in a chat transcript.

## Production is prepared, collected and graded — 2026-09-18

The runbook has been **run against `sports_picks.db`**, each step dry-run first
and checked against what it produced on a copy. Backup, taken after a
`wal_checkpoint(TRUNCATE)` so the `.db` file is complete:
**`sports_picks.backup-20260918-222023.db`** (`integrity_check: ok`, 708 picks,
1664 games). A second, pre-collection backup:
`sports_picks.backup-20260918-223905.db`. They are the only rollback.

| | before | after |
|---|---|---|
| games | 1664 | **1713** |
| — final | 1058 | **1363** |
| — past, not final | 570 | **314** |
| games with `espn_id` | 0 | **1371** |
| duplicate `espn_id` | 0 | **0** |
| picks / max id | 708 / 708 | **708 / 708** |
| **`pick_results`** | **0** | **173** |
| — props graded | 0 | **75 of 82** |
| **`player_stats` `game_log`** | **0** | **26,843** |
| — players with ≥3 logs | 0 | **571** |
| elo_history | 2116 | 2726 |

`integrity_check: ok`. Graded: moneyline 18W/18L, over_under 15W/22L,
spread 14W/11L, **prop 48W/27L**. 535 picks remain ungraded — mostly on the
314 games still not final (ncaab and mma/boxing).

### The first prop calibration from production

```
  tier   settled   wins  losses   win%     units     roi  reliable
  5         36     27       9    75.0%    +8.62  +0.239    NO
  4         16      8       8    50.0%    -3.66  -0.229    NO
  3         10      7       3    70.0%    +0.42  +0.042    NO
  2         10      5       5    50.0%    -2.19  -0.219    NO
  1          3      1       2    33.3%    -1.07  -0.358    NO

  5-star vs 4-star: 75.0% vs 50.0% -- higher by 25.0 points.
  tier 5: n=36 across 2 games -> effective 19.5 (ICC 0.05), 10.1 (ICC 0.15)
```

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.prop_calibration --db <abs win path> --sport nba
```

**These are identical to the figures the copy produced, which is the good
news** — the production pipeline reproduced it exactly. **It is also the
warning**: the sample is still the same **2 games**. Every tier is flagged
unreliable, and tier 5's effective n is 19.5 against a `min_bin` of 30.

**How badly the headline moves on partial data:** a run taken when only one of
the two games had been graded reported tier 5 at **61.5%**. The same props, the
same model, a 13-point swing from which game happened to be graded. Quote the
effective n, never the raw win rate.

**`digest.enabled` stays `false`.** What changed is that this measurement is
now possible and repeatable in production — not that the model is good.

### Two collector bugs found by doing this for real

1. **Box scores were skipped per DATE, not per game.** The resumability check
   filtered on `(sport, game_date)`, so on any date with more than one game
   only the first was ever collected: **176 of 1248 final games, 3826 rows
   where the true figure is 26,843**. It surfaced because a game's props could
   not be graded when another game shared its date. `PlayerStat` has no
   `game_id`, so the check now also filters on the game's own `team_id`s.
   Fixed in `cc9eee2`. **The plan 010 test meant to guard resumability used one
   game per date, so the distinction was invisible to it.**
2. **The catch-up asked only about the stored date.** Game 1603 holds 48 props
   and stayed non-final because it sits on 2026-05-25, the UTC date of an 8pm
   ET tip, while ESPN files event `401873200` under 05-24. Now searches ±1 day,
   the same window `backfill_espn_ids` already used. Fixed in `703e6ff`.
   **That fix only works because plan 014 populated `espn_id` first** — without
   an id the fallback still matches on the wrong date and inserts a twin. The
   test encodes that dependency rather than hiding it.

Both are the session's recurring shape: **a guard that was right about the case
it imagined and blind to the one that mattered.**

### Still open after all this

- **7 props ungraded** — players ESPN's box score does not list. DNPs produce
  no row by design (absent is not zero), so this is correct behaviour.
- **314 past games still not final**, almost all ncaab (59) plus mma/boxing,
  which are out of scope for the ESPN scoreboard path.
- **ncaab duplicates.** The catch-up inserted rows for ESPN events whose
  abbreviations do not match our display-name team rows, so ~10 real games now
  exist twice. **429 of 708 picks are on ncaab.** Needs the team-identity fix
  before ncaab can be trusted; nba and mlb are unaffected.
- **More games.** Everything above rests on two nights of props. The scheduler
  is still not running; starting it is now safe for nba/mlb.

## ncaab team identity repaired in production — 2026-09-19

Plan 015 run end to end against `sports_picks.db`. Scheduler stopped first
(PIDs 11760/8460 under `nohup`); backup taken after a
`wal_checkpoint(TRUNCATE)`: **`sports_picks.backup-20260919-002602.db`**
(`integrity_check: ok`, 1713 games, 708 picks, 629 teams). It is the only
rollback.

| | before | after |
|---|---|---|
| ncaab team rows | 147 | **87** |
| — holding a display name | 78 | **1** |
| ncaab games | 120 | **86** |
| — final | 61 | **72** |
| — still not final | 59 | **14** |
| — missing `espn_id` | 59 | **12** |
| ncaab picks | 429 | 429 |
| — **graded** | **16** | **348** |
| total games | 1713 | 1679 |
| total picks / `pick_results` | 708 / 173 | 708 / **505** |

`integrity_check: ok`, 0 duplicate ncaab fixtures, abbreviations unique,
0 orphaned picks. **No picks and no final games were lost** — the 34 games
removed were empty duplicate rows.

### What was actually wrong

`_ensure_game_from_odds` created missing teams as
`Team(abbreviation=<display name>)` for every sport, though its docstring
limited that to sports without ESPN coverage. ESPN sends `PENN`; a row
holding `'Pennsylvania Quakers'` never matched, so those games never got an
`espn_id` and never finalised. Fixed in `a18bae8`: labels now resolve through
`backend/team_identity.py`, and a game whose team cannot be identified is
skipped rather than backed by a row that can never match.

**Two corrections to what this file previously claimed:**

1. It said ncaab's problem was "~10 real games now exist twice". The measured
   shape is **59 stranded originals**, and separately **34 duplicates** that
   only become visible once the team rows merge — while the two schools hold
   different team ids, a same-teams/same-date scan reads the twins as
   different fixtures. Both were true at once.
2. A survey identifying bad rows as `length(abbreviation) > 5` missed team
   327, which holds `'QUC'`. Short is not the same as valid.

### The survivor rule, measured

`resolve_duplicate_games` keeps the **final** row. All 34 groups were
`('final', 'scheduled')`: the final row carries the score, `espn_id`,
`team_stats` and `elo_history`; the scheduled twin carries the picks and odds.
**In 27 of 34 the picks sit on the non-final row**, so
`merge_duplicate_games.py`'s "row with picks wins" would have discarded real
scores in 79% of these cases. Do not reuse it here.

### Two open items this surfaced

1. **33 graded picks carry invalid `odds_at_pick`** (−99..−61; valid American
   odds are ≤ −100 or ≥ +100). `payout_for` correctly refuses to price them
   and books 0.0. Because a loss books −1.0 regardless of odds, only the
   **16 wins** are zeroed — so **ncaab ROI is biased downward, not merely
   noisy**. Any unit figure below is a floor. Where `odds_at_pick` acquires
   these values is not yet traced.
2. **12 ncaab games still have no `espn_id` and 14 remain non-final.**
   `unknown_team` in `backfill_espn_ids` dropped from 7 to **0**, so the
   identity problem is gone; what is left is ESPN genuinely not listing
   those fixtures on the dates we hold.

### First ncaab grading

```
  ncaab picks graded : 348 of 429   (141 win / 207 loss = 40.5%)
  units              : -83.8   <- understated; 16 wins booked at 0
```

**Do not read 40.5% as a model verdict yet.** 348 picks sit across 72 games —
about 4.8 per game, and picks within a game are correlated, so the effective
sample is far smaller than 348. Quote effective n, as
`backend/analysis/prop_calibration.py` already does. `digest.enabled` stays
`false`.

## First ncaab calibration, and the Clippers row — 2026-09-19

The nba display-name row (team 205 `'Los Angeles Clippers'` vs team 15 `LAC`)
was repaired with the same general tool, not a one-off script. It also
surfaced a defect class the ncaab run did not: **date-offset twins**.

`resolve_duplicate_games` groups by exact date, so it merged the team row but
left both duplicate *games* behind — they sit one day apart, the plan-014
UTC-vs-ET shape. `resolve_offset_twins` handles them, with a deliberately
narrow signature: same teams exactly one day apart, exactly one row final
**and** carrying an `espn_id`, the other with no score, no `espn_id`, not
final. Production holds **47** same-team pairs a day apart; only **2** match.
The other 45 are genuine back-to-backs and mma cards. A looser rule deletes
real games.

Like same-date duplicates, offset twins are invisible before the merge: the
survey found **0** against production and **2** against the merged copy.
Backup before this step: `sports_picks.backup-20260919-004133.db`.

Final: 708 picks / **519** graded, 1675 games, `integrity_check: ok`,
no picks lost. `STARS`/`WORLD`/`STRIPES`/`TBD` are All-Star rosters and a
placeholder; they stay unresolved and untouched, with a test.

### ncaab results — read the breakdown, not the total

```
  type           n     W     L    win%     units      roi
  over_under   120    61    59   50.8%     -3.55   -0.030
  spread       116    51    65   44.0%    -18.64   -0.161
  moneyline    112    29    83   25.9%    -61.62   -0.550
  TOTAL        348   141   207   40.5%    -83.80   -0.241
```

**Moneyline is where the money goes**: 26% win rate and -0.55 ROI, carrying
**74% of all losses**. Over/under is close to break-even. Any triage starts
here, and it is a far more specific finding than the -83.8 total.

By confidence tier the signal does point the right way:

```
  tier 5  n=120  W=61  50.8%  units  -3.55
  tier 1  n=228  W=80  35.1%  units -80.26
```

**Caveats, all of which matter more than the numbers:**

- **Effective n is near 37, not 348.** The 348 picks sit across 37 games,
  9.4 per game, and picks within a game share teams, pace and outcome. A
  15-point tier gap across ~37 clusters is directional, not established.
- **Units are understated.** 16 wins carry invalid `odds_at_pick` (-99..-61)
  and book 0.0. Priced at -110 the total would be about **-69**, but the true
  prices are unknown — that is the defect, not a correction.
- **`calibration_report --sport ncaab` gives Brier 0.2116 on 37 games**, with
  observed rates *above* predicted in every populated bin. So the win model
  looks underconfident while the picks lose. Those are different objects: a
  reasonable win probability can still produce losing bets if the edge or
  line comparison is wrong. **That gap is the thing to investigate next.**
- The three dead features (`offensive_rating`, `defensive_rating`, `pace`)
  still carry no signal; the Brier above was produced without them.

**Prop calibration is unchanged** — still the same 2 nba games, every tier
under `min_bin=30`, tier 5 effective n 19.5 (ICC 0.05) / 10.1 (ICC 0.15).
The repair added ncaab game-level picks, not nba props.

**`digest.enabled` stays `false`.**

## Invalid odds_at_pick traced and reconstructed — 2026-09-19

34 ensemble moneyline picks (2026-03-15..21) stored a price inside the
invalid American-odds band (-87..-61). Root cause, reproduced on all 34:

```
stored == sum(moneyline prices) / count(ALL odds rows for the game)
                                  ^^^ not count(rows carrying a price)
```

Books posting only a spread and total were counted in the divisor, pulling
the mean toward zero. Game 1401: 8 rows, 4 with a moneyline summing to -574;
correct consensus -143, dividing by 8 gives -71. A second, independent defect
is present too — pick 522 had 7 prices of 7 rows (no dilution) and still
stored -76 against a correct -105, from averaging in price space rather than
probability space.

**Not established:** every committed `ensemble.py` back to 2026-03-13 uses
`len(ml_home)`, the correct divisor. Either deployed code differed from what
is committed, or those now-NULL columns held `0` at the time. The effect is
identical either way and the current code is correct on both counts.

### The repair

`backend/scripts/repair_invalid_odds.py`, dry-run by default. It calls the
same `consensus_moneyline` the live strategies use — extracted from
`Strategy._average_odds`, which now delegates to it — so a reconstructed
price cannot drift from one recorded today.

| | |
|---|---|
| repaired | 34 |
| payouts changed | 16 (exactly the zeroed wins) |
| unrecoverable | 0 |
| picks left in the invalid band | 0 |
| wins still booked at 0.0 | 0 |

Backup: `sports_picks.backup-20260919-010520.db`.

**These are reconstructions, not recordings.** The prices come from the book
rows that survive today; their timestamps are at or before each pick, but
they are not provably the snapshot the pick was taken against. Every repaired
row carries **`picks.odds_reconstructed = 1`** so the distinction lives in
the data rather than in this file. `ncaab units -83.80 -> -73.33`.

### ncaab after the repair

```
  type           n     W    win%     units      roi
  over_under   120    61   50.8%     -3.55   -0.030
  spread       116    51   44.0%    -18.64   -0.161
  moneyline    112    29   25.9%    -51.14   -0.457
  TOTAL        348   141   40.5%    -73.33   -0.211

  excluding reconstructed rows:
               316   125   39.6%    -67.80   -0.215
```

The two subsets agree closely (-0.211 vs -0.215), so the reconstruction is
not carrying the conclusion. **Moneyline remains the problem**: 26% and
-0.46 ROI, still ~70% of all losses. Effective n is near 37 games, not 348.

### A schema-drift bug this surfaced

`picks.odds_reconstructed` first went in with a Python-side default only,
so `create_all()` (tests, fresh databases) produced NOT NULL **without** a
DB default while the migration produced `DEFAULT 0`. Any raw INSERT then
failed — four tests errored. Fixed with `server_default="0"`. **`created_at`
has the same latent shape**: it is NOT NULL with a Python-side default, so
raw-SQL inserts into `picks` must supply it explicitly.

## The scheduler now survives a reboot — 2026-09-19

Registered Windows task **`sports_picks scheduler`**, running
`scripts\start_scheduler.ps1` **at logon** as `mwill` (Interactive, Limited).

| setting | value | why |
|---|---|---|
| `ExecutionTimeLimit` | `PT0S` | unlimited. The default is 3 days, which would kill a long-lived scheduler mid-week |
| `MultipleInstances` | `IgnoreNew` | the task cannot run twice |
| trigger delay | `PT1M` | let the network settle before the first ESPN/Odds call |
| restart | 3 attempts, 5 min apart | |

**Why logon and not startup.** The pipeline reads the shared secrets file
under `~\.secrets` and runs from a per-user venv. An at-startup task runs as
SYSTEM, with no user profile; running at startup *as* the user would require
storing the account password. Logon needs no credentials and gives the
process exactly the environment it has when started by hand.

**The trade-off, stated rather than buried:** if the machine reboots and
nobody logs in, the scheduler does not start. A reboot at 03:00 with a 09:00
login misses that day's 08:00 `morning_scout` — `scout_retry_9` and
`scout_retry_10` exist for roughly this case. Switching to SYSTEM-at-startup
is a one-line change in `register_scheduler_task.ps1`, but the secrets path
must be re-verified under that account first.

### Verified, not assumed

Task Scheduler reporting "Ready" is not a running pipeline. The scheduler was
killed and the task fired the way logon would: `LastTaskResult 0`, the
process came up, 4 jobs registered, 0 errors. Firing the task a second time
returned the **same PIDs** — `start_scheduler.ps1` checks for a running
`backend.pipeline.scheduler` first, because Task Scheduler's `IgnoreNew`
stops the *task* running twice but not a task-started scheduler colliding
with a hand-started one. Two instances would share `sports_picks.db` and run
every cron job twice.

### Logs

`scheduler.log` is wired to **stderr**, because `logging.basicConfig` writes
there — the first version put every INFO line in `scheduler.err.log` and left
`scheduler.log` at 0 bytes. `scheduler.out.log` catches stray stdout and is
normally empty. Both rotate to `.prev` on start, so a restart no longer
destroys the log covering whatever went wrong. Both are gitignored.

### The legacy .bat/.vbs launchers are gone

`restart.bat`, `start-server.bat`, `start-server-loop.bat`,
`start-tunnel.bat`, `start-tunnel-loop.bat`, `start-server-hidden.vbs`
and `start-tunnel-hidden.vbs` were deleted on 2026-09-19. All seven are
in git history if one is ever wanted back.

Five pointed at the OneDrive path dead since July 2026, and the server
launchers used system Python 3.14 rather than the venv. `restart.bat`
also ran `taskkill /F /IM python.exe`, which kills **every** Python
process on the machine -- the scheduler and every MCP server. The two
tunnel scripts did still work (`cloudflared.exe` is present) but only
served a server started by the broken loop script.

Launching is now `scripts\start_scheduler.ps1` for the pipeline, and
`Dockerfile` / `deploy/sports-picks-web.service` / `start.sh` for the web
app -- all three confirmed present before anything was removed. The
docstring at `backend/api/main.py:138` listed two of the deleted files as
live launch sites and has been corrected.

## Why the moneyline picks lose — 2026-09-19

142 graded moneyline picks across **54 games**. ncaab loses 51.14 units at
-0.457 ROI; nba *makes* 23.84 at +0.795. That split is the clue.

### The claimed edge is inverted

Holding price roughly constant, inside big underdogs only:

```
  edge  0-20%   n=6    50.0% win   avg price +787   roi +3.213
  edge 20-35%   n=24   20.8% win   avg price +359   roi -0.123
  edge 35-50%   n=36    0.0% win   avg price +907   roi -1.000
```

**36 picks claiming a 35-50% edge, at an average price of +907, won zero
times.** The comparable +787 group with a *smaller* claimed edge won half. A
larger claimed edge does not merely fail to predict return — it predicts
return in the wrong direction. This is not the big-underdog concentration:
price is held roughly constant across the top and bottom rows.

### The mechanism: one home-advantage intercept for every sport

`CalibratedModel.train_from_db` (`backend/analysis/calibrated_model.py:97`)
selects every `status='final'` game with **no sport filter**, and fits one
logistic regression. `sport` appears in that module only at lines 75-76,
inside `_fallback_probability` — the heuristic used when the model is *not*
trained. The trained model has no notion of sport at all.

`extract_features` sets **`"home_flag": 1`** — a constant, in training and at
prediction. So home advantage cannot be a learned coefficient; it lives
entirely in the intercept, and there is only one.

```
  sport     final games   actual home win%
  nba              1248              0.554
  ncaab              72              0.708
  mlb                54              0.519
  POOLED           1374              0.561   <- the single intercept
```

nba is **91%** of the pool, so the intercept is effectively nba's 0.554,
applied to ncaab whose reality is 0.708 — a **~15 point** underestimate of
home advantage. Away teams in ncaab are therefore overrated by roughly that
much, which manufactures exactly the edges observed: large, concentrated on
away underdogs, and wrong.

### Three independent corroborations

1. **Side split.** AWAY n=112 at 24.1% (-0.264 ROI); HOME n=30 at 46.7%
   (+0.077). And big-dog AWAY n=61 wins **6.6%** while big-dog HOME n=5 wins
   80%.
2. **The generator prefers home.** Pick generation is
   `if home_edge >= min_edge: ... elif away_edge >= min_edge:` — home is
   tested first, so home picks are structurally favoured. Away still
   outnumbers home **112 to 30**. That only happens if `away_edge` is
   systematically much larger.
3. **nba is profitable, ncaab is not.** A natural experiment: the pooled
   intercept happens to fit nba (0.554 vs pooled 0.561) and misfit ncaab
   (0.708). nba moneyline returns +0.795 ROI; ncaab -0.457.

### Caveats

- **Effective n is ~54, not 142.** Picks cluster within games.
- **ncaab's 0.708 rests on 72 games** (±~5 points). Even the low end of that
  interval sits far above 0.561, so the direction is safe; the magnitude is
  not precise.
- The 0-for-36 group is concentrated in a small number of game-days.
- nba's +0.795 is 30 picks. Directional.

### Two data gaps found on the way

- **`model_prob` is NULL on all 708 picks**, every type. Not a live bug: the
  wiring landed 2026-09-16 (`4bb2cf2`) and the newest pick is 2026-05-24.
  Future picks will carry it. Until then the model's own predicted
  probability cannot be compared against outcomes directly — which is the
  measurement that would confirm the above rather than infer it.
- **`home_flag` is a fourth dead feature**, alongside the three
  (`offensive_rating`, `defensive_rating`, `pace`) the calibration report
  already names.

### What would fix it

Train per sport, or add sport as a feature so the intercept can differ. Given
ncaab has only 72 final games — well above `MIN_TRAINING_GAMES = 30` but thin
— per-sport training would make ncaab's model very small. A sport dummy in
the pooled fit keeps the sample and lets the baseline move. Either way the
fix is testable offline: assert the fitted home baseline for ncaab lands near
0.70 rather than 0.56.

**Do not re-tune `min_edge` first.** The edge estimate is inverted above 10%;
raising the threshold selects harder for the defect.

## First scheduled run reviewed — 2026-09-19 08:00 ET

`morning_scout` fired on time and the scheduler is healthy. The structural
work all succeeded; the two things that produce *value* both failed.

```
  Stored 71 ncaaf games for 2026-09-19        <- ESPN fine
  Refreshed 1136 team_stat values             <- fine
  Scheduled ncaaf window: 11 games, run 09:30 <- fired, completed
  Prop pipeline: games 86, stats_fetched 0, props_analyzed 0, picks 0
  Window complete. Credits today: 0, month: 0/20000
```

| | today |
|---|---|
| games stored | **86** |
| odds rows | **0** |
| picks created | **0** |
| newest pick in the database | still 2026-05-24 |

### P0 — the Odds API key is the one that was rotated out

Every Odds API call returns **401 INVALID_KEY**, so no odds were stored and
therefore no picks could be generated. It is not a subscription or sport
problem:

| key | `/v4/sports` |
|---|---|
| repo `.env` → `ODDS_API_KEY` (file dated 20 Mar 2026) | **401 INVALID_KEY** |
| `~\.secrets\shared.env` → `TheODDSAPI` | **200**, 8736 credits left, ncaaf/nfl/mlb all offered |

Nothing bridges the two. `backend/config.py:14` reads only
`os.environ.get("ODDS_API_KEY")`, and `load_dotenv()` at `config.py:7` reads
only `./.env` — which still holds the pre-rotation key. The rotated key has
been sitting in the shared secrets file under a different name the whole time.

`.env` is gitignored and was never committed, so the stale key did not leak.

**This is the single thing standing between the pipeline and producing
picks**, and it matters today: ncaaf is in season (71 games on 2026-09-19),
nfl and mlb are live, and nba does not start until 22 Oct.

### P1 — `EspnStatsSource._find_team_id` does not exist

`espn_stats_source.py:52` calls `self._find_team_id(sport, team_abbr)` from
`fetch_season_averages`, and the method is defined nowhere on the class.
**284 warnings in one run** — one per team — and the prop pipeline reported
`stats_fetched: 0`. This was already open item 9; it is now confirmed firing
in production on every scheduled run.

### Not a problem

- The 4 ncaaf 401s are the same key fault, not a separate issue.
- `Credits today: 0` is consistent: every call 401'd, so nothing was billed.
- The scheduler itself, the logon task and the log rotation all behaved.

## Season averages collect again — 2026-09-19

Two defects in a chain. The first hid the second.

### `_find_team_id` was never written

`fetch_season_averages` called `self._find_team_id(sport, team_abbr)` and the
method existed nowhere on the class. Every call raised `AttributeError`, the
collector caught it and logged a warning, and nothing failed loudly: **284
warnings in one scheduled run**, `stats_fetched: 0` every time.

It is now `backend.team_identity.espn_id_for(sport, abbreviation)`, reading
ESPN's numeric id from the **committed snapshots** rather than fetching
`/teams`. No network call, and it cannot drift from the abbreviations the
rest of the pipeline resolves to, because both read the same file. A test
runs it against a client that raises on any use, so reaching for the network
fails the suite.

### Fixing that exposed a dead endpoint

With the `AttributeError` gone, execution reached the next call — which 404s
for every athlete:

| endpoint | |
|---|---|
| `site.api.../v2/.../athletes/{id}/statistics` | **404** (what the code used) |
| `site.web.api.../common/v3/.../athletes/{id}/stats` | **200** |

The v3 shape is different, not just the URL: parallel arrays, with
`categories[].labels` alongside `categories[].statistics[].stats`. The old
parser walked `statistics[].splits[].categories[].stats[]`, which that
endpoint no longer returns.

**Basketball**: one `averages` category, one row per season, **oldest
first** — so the last row is the current season. Taking `[0]` would report a
player's rookie year as their form.

**Football**: categories are `passing` / `rushing` / `receiving` / `scoring`,
and **`YDS` appears in three of them**. The map is keyed by
`(category, label)` for exactly that reason; a flat label map would report
whichever category came last as all three yardage figures. Touchdowns come
from `scoring.TD` (27), not `passing.TD` (21) or `rushing.TD` (6).

Two value shapes needed handling: thousands separators (`"1,912"`) and
made-attempted pairs (`"2.5-6.0"`, where the made half is the statistic). A
leading minus survives, so `-2` receiving yards is not mangled.

Verified against a live payload:

```
  parsed: minutes 33.2, points 20.9, rebounds 6.1, assists 7.2,
          threes 1.3, steals 1.2, blocks 0.6, turnovers 3.0
```

### Not verified end to end

ESPN began returning **403** on the roster endpoint partway through this
work — the same URL had returned a full roster minutes earlier, so it is
rate limiting, not a defect. Roster → stats → parsed values has therefore
**not** been run as one live chain. Each leg is verified separately: the
roster endpoint returned real athletes before the 403, the v3 stats endpoint
returns 200, and the parser produces correct values from a real payload.

**Confirm at the next scheduled run.** `scheduler.log` should show the 284
`_find_team_id` warnings gone and `stats_fetched` above zero. If it does not,
the 403 is persistent and the collector needs a backoff, which it does not
currently have.

### A splice accident worth knowing about

Replacing the parser methods by index removed `is_available` and `close`
along with them, which made `EspnStatsSource` abstract and unconstructible.
Caught by the tests immediately and restored from `HEAD`. Editing Python by
locating "the next `def`" is unreliable when the file mixes `def` and
`async def`.

## Where things stand

`master` is at `cc9eee2` and **pushed**. Test baseline: **646 passing backend,
0 failed**, identical on Python **3.12 and 3.14**; frontend eslint 0/0, `tsc` clean, vitest 15/15.
Started this session at 545.

**CI is live** (`.github/workflows/ci.yml`), ~50s for all three jobs. Local:

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

`master` is the repository's only branch and its default.

### The app runs

```
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

`/health` answers, the React bundle serves from `frontend/dist`, and
`/games/today`, `/picks/today` and `/users/feed` all return 200. Empty arrays
are correct — there are no games dated today.

(`start-server.bat` carried a warning here about its dead OneDrive
path; it was deleted on 2026-09-19.) **Be careful with `start.sh`**: it also launches `python -m backend.pipeline.scheduler`,
which ingests — see the deploy-order warning below. The scheduler is off by
default (`ENABLE_SCHEDULER` unset), so plain uvicorn is safe.

### This session (2026-09-16 evening → 09-17)

The session crossed local midnight, so timestamps differ by source: the DB
backup is stamped `20260916-231622` (local) while CI logs `06:xxZ` (UTC) the
next day. Same session.

| Commit | What landed |
|---|---|
| `c292068` | `compute_historical_elo` stopped implementing its own Elo replay |
| `9de96b8` | Calibration report stopped printing a false "LOWER BOUND" caveat |
| `fc9a3f4` | **Live `sports_picks.db` backfilled** — production had never had the 008 data |
| `9ff809e` | CI wired: backend matrix 3.12/3.14 + frontend |
| `e5e083d` | Dependencies pinned via `constraints.txt` |
| `72416da` | `sports_picks.egg-info` untracked |
| `6117d5e` | **010 T1** — `grade_pick` refuses instead of inventing a loss |
| `fb50170` | **010 T3** — ESPN post-game box-score collector |
| `cfb276d` | **010 T2** — `PickModel` carries prop player and market |
| `935ed15` | **010 T4** — props routed to the prop grader, payouts priced |
| `8a286c3` | **010 T5** — prop confidence measured against outcomes |
| `3246256` | **011** — the ASGI app builds on access, not at import |
| `5603514` | **013 T1-2** — finalize games played since the last run |
| `1c75c11` | **013 T3** — one-off catch-up for games never marked final |
| `eddf502` | **014 T1** — `Game.espn_id`, identity matching |
| `a5f9201` | **014 T1** — `espn_id` backfill script |
| `e04fa24` | **014 T2** — merge games stored twice, once per date convention |
| `822f5ef` | **014 T3** — dates are Eastern, via one shared `time_utils.et_date` |
| `adb66d8` | **012 T1** — dead recent-form fetch removed |
| `033d712` | **012 T2** — recent form bounded to before the game |
| `6c8c62e` | **012 T3** — one edge formula, on one scale |
| `e90049a` | **WebSocket transport installed** — `/ws` can finally upgrade |
| `a7112d7` | Box scores fetched by stored `espn_id` instead of searching |
| `703e6ff` | Catch-up asks about the day's neighbours too |
| `cc9eee2` | **Box scores skipped per game, not per date** — 176 of 1248 collected before this |

**Plans 001-011 and 014 are complete. 013 is done bar the production
catch-up. 012's Tasks 1-3 are done and Task 4 is blocked on data.**

### The findings worth carrying forward

Every one of these is the same shape: **a well-formed wrong value, or a
component that works while the composition does not.** None raised, none
logged an error, and several had passing tests.

1. **Nobody asked ESPN about yesterday.** `morning_scout` fetched `today` at
   8/9/10am ET — before that day's games were played — and nothing ever
   revisited a past date. **570 games with past dates were stuck non-final**,
   so grading, box scores, `game_log` and `elo_history` growth were all
   dormant. The upsert that finalizes a game was correct and unreachable.
   Fixed in `5603514` (3-day finalize-only lookback).
2. **ESPN dates are UTC; its scoreboard is Eastern.** Every game after 8pm ET
   was filed a day late, so the same game existed twice — once per convention.
   Fixed in `822f5ef`, but only after `eddf502`/`e04fa24`, because flipping the
   date first would have *doubled* the duplicates.
3. **Nine real games were counted twice in the Elo replay.** Both halves of a
   twin pair were final, so `backfill_elo_history` applied nine results twice.
   A full replay moved **957 of 2098 pre-game ratings**, median 0.32, p90 6.81,
   **max 20.64 points** — and `elo_history` is what the calibrated model trains
   on. **Every Brier number measured before that replay describes corrupted
   ratings.**
4. **Prop projections never saw recent form.** `recent_weight` is 0.6, so it is
   60% of every projection, and `game_log` was empty — every prop ever
   generated used 100% season average. The probability model never ran either:
   `use_distribution` needs three game-by-game values and has always been
   `False`, so every prop used `abs(diff / line) * 100`, which is not a
   probability and inflates small lines (a 0.5 line reported a 140% "edge").
   Fixed in `6c8c62e`.
5. **Importing `backend.api.main` migrated whatever `sports_picks.db` was in
   the cwd.** Running pytest from the repo root was enough. Harmless only
   because migrations are additive — `migrate_api_usage` contains a
   `DROP TABLE`. Fixed in `3246256`.
6. **The WebSocket feature was dead everywhere it actually runs**, and no
   functional test could have caught it. `uvicorn` was declared without a
   WebSocket library, so `/ws` answered the upgrade with a plain 200.
   `TestClient` implements WebSocket **in-process** using Starlette's own code
   and never touches uvicorn's transport, so plan 004's 15 tests all passed
   against a path production never uses. Found by launching the app; fixed in
   `e90049a` with a **dependency** guard rather than a functional one.
7. **The local venv was the stale environment, not CI.** `pyproject.toml` had
   floors only and both `Dockerfile` and CI ran a bare `pip install -e .`, so
   both tracked latest while the venv sat months behind — starlette 0.52.1
   locally against 1.6.0 everywhere else. `constraints.txt` now pins all four
   environments to one set.
8. **The deployed runtime had never run the test suite.** `Dockerfile:17` pins
   `python:3.12-slim`; the venv is 3.14. The matrix closed that, and 3.12
   passes — the risk was latent, not active.

### Merged into `master`

| Plan | What landed |
|---|---|
| 001 | Characterization tests pinning the paper-trading money path |
| 002 | Calibrator trains (`status=="final"`), recalibrated thresholds reach `calculate_confidence`, `receptions` persisted |
| 003 | Vig removed in all five strategies; odds averaged in probability space; one bad game no longer discards a batch |
| 004 | Activity feed works — `/users/feed` returns 200, real WebSocket frames delivered |
| 005 | Odds API key kept out of logs and responses; budget 429 reachable |
| 006 | Per-sport, push-aware, newest-first recalibration |
| 007 | Out-of-sample calibration report (a read-only measurement tool) |
| 008 | The model trains on real point-in-time features for the first time |
| 009 | Frontend ESLint clean — 6 errors / 2 warnings → 0 / 0 |
| — | Daily picks digest (six-task feature, dry-run by default) |

### All nine plans are now merged (historical — 010-014 came later)

**`advisor/008-populate-team-stats` — MERGED** as `bb99490`. Seven commits: the
original five, plus two review rounds. Report:
`.superpowers/008-team-stats-report.md`.

Verified post-merge on `master`: **545 backend tests passing, 0 failed**
(500 before + 45 from 008). Frontend unchanged: eslint 0/0, tsc clean,
vitest 15/15.

What it fixed: nothing in production wrote a `TeamStat` row, so the model
trained on 1058 games with 4 of 5 features identically zero, and `elo_diff`
fell back to a *current* `EloRating` for historical games — lookahead. That is
the root cause of the model claiming 99% where the de-vigged market said 84.5%.

| | |
|---|---|
| `team_stats` distinct game_ids | **1 → 1058** |
| `elo_history` rows | **0 → 2116** |
| `rest_days` | present for the first time |
| Live `sports_picks.db` | backfilled **2026-09-16** (was untouched during 008) — see below |
| Fabrication check | `pace` / `offensive_rating` / `defensive_rating` still present for **exactly 1** game. Structurally refused, not fabricated. |

**Brier ROSE 0.1836 → 0.2020, and that is the honest result.** The old number
came from a model whose only non-zero coefficient was an end-of-season Elo
rating — constant per team and unknowable before tip-off. 0.2020 is the first
measurable score. The model is still badly calibrated: overconfident on
underdogs by 15-19 points.

> **Superseded by the 2026-09-17 re-run.** The 007 report on the backfilled
> data gives **0.2032**, not 0.2020 — the small difference is consistent with
> `c292068` changing draw handling in the Elo replay. Treat **0.2032** and the
> bin table in "Calibration baseline" below as current; the numbers here are
> what 008 measured at the time.

#### The two review rounds, and why they matter

Both found the same failure shape — a value's basis changed and only some
readers were updated:

1. **Round 1**: commit `553bc41` fixed the *training* half of the Elo lookup and
   left live serving reading `EloRating`, a table written only by
   `compute_historical_elo`, whose entry point `load_historical_data` has **no
   callers**. Frozen at whatever a past manual run left. Trained and served
   ratings diverged by up to 134 points in both directions, so no intercept
   absorbed it. Fixed in `8189510` by adding a most-recent-prior-`EloHistory`
   step, strictly before the predicted game's date.
2. **Round 2**: that fix displaced the same defect into `_check_lookahead_spot`,
   which compared a history-basis `team_elo` against an `EloRating`-basis next
   opponent across a 50-point band — while the bases differed by a mean of 53.
   Fixed in `f4b93eb`. Note the obvious one-line fix was a trap: bounding by
   `next_game.date` or passing `next_game.id` would each have converted a basis
   bug into a *lookahead* bug.

Both rounds are mutation-proved. `_check_lookahead_spot` had no direct test
before round 2; it now has five.

#### Known residuals, deliberately left

- ~~**`backtesting.historical.compute_historical_elo` still writes post-game
  ratings into the `elo_history` column that now holds pre-game ones.**~~
  **RESOLVED 2026-09-16.** It no longer implements a replay at all: it
  delegates to `pipeline.team_stats.backfill_elo_history`, the function the
  daily pipeline already calls, and keeps only the `EloRating` upsert (fed by
  a new `final_ratings` key on the delegate's return). Delegation also gave it
  two guards it never had — it skips games already in the history instead of
  appending duplicates, and it refuses combat sports. That second one was a
  latent mirror of the same bug: `SEASON_RANGES` accepts `mma`/`boxing`, so the
  naive fix would have written *pre*-game rows into the grader's *post*-game
  history. Three tests added, all watched failing first and mutation-proved.
- `EloRating` is now routed around rather than fixed for team sports. Resolve
  the convention clash above first, then decide whether to wire it up or delete
  it.
- 14 of 239 upcoming NBA games still fall through to `EloRating` — teams with no
  finals, so no history to replay. 5.9%, documented, on the old basis.
- **Two of five features remain structurally constant** (`pace`,
  `offensive_rating` / `defensive_rating` need possessions). The modelling
  choice — drop them, source possessions, or leave them defaulted — is the
  natural next decision.
- `models.py` declares **no indexes** on `games`, `elo_history` or
  `elo_ratings`. Noise at ~2k rows; revisit if `elo_history` grows an order of
  magnitude.
- 007's `calibration_report.py` docstring documents the now-fixed data defect as
  a "known limitation". That paragraph is stale.
- `_check_lookahead_spot`'s thresholds (100-point favourite, 50-point band,
  4/10-day windows) are uncalibrated constants feeding a real pick adjustment.
  Correct basis now, unexamined thresholds.

**The success-shaped failure did not occur.** A post-backfill run that looked
*excellent* would have suggested leakage; instead Brier got worse with a
coherent explanation, and the fabrication check came back clean.

## How to resume

```bash
cd C:/Users/mwill/Documents/mwilliams2733/sports_picks
git worktree prune          # clears stale entries pointing at deleted temp dirs
git worktree list           # should show only the main tree
git log --oneline -12
```

Then, for whichever plan you pick up, recreate a worktree:

```bash
git worktree add -b <branch> <path> <base-commit>
```

**The worktrees have no `.venv`** (it is gitignored). Use the main tree's
interpreter by absolute path, and run pytest from the worktree root so `backend`
resolves to the worktree copy rather than the main tree:

```
C:/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python.exe -m pytest backend/tests -q
```

Verify that once with
`... -c "import backend; print(backend.__file__)"` — it must print a path inside
the worktree.

That check matters more than it looks. `import backend` resolves by **cwd**,
not by the editable install, and on 2026-09-17 the editable install was found
pointing at `c:\users\mwill\onedrive\...` — a directory deleted in July 2026.
Everything still worked, because cwd won every time. A dangling editable
install is invisible until the day cwd is not what you assume.

### Environment

The venv is **Python 3.14**; production (`Dockerfile:17`) is **3.12**. CI runs
both. Dependencies are pinned in `constraints.txt`, consumed by both the
Dockerfile and CI — do not `pip install --upgrade` casually, since that
silently desynchronises the venv from the pinned set. To upgrade deliberately:

```
.venv/Scripts/python.exe -m pip install -e ".[dev]" --upgrade --upgrade-strategy eager
.venv/Scripts/python.exe -m pip freeze | grep -v "^-e " > constraints.txt   # then restore the header
```

Plain `--upgrade` is a no-op here: pip's default `only-if-needed` strategy
leaves already-satisfied dependencies alone. `eager` is required. Push and let
the 3.12/3.14 matrix confirm the new set before trusting it.

## Open plans

**Plans 001-011 and 014 are complete.** 013 is done bar the production
catch-up. **012's Tasks 1-3 are done; its Task 4 is blocked on data.** `plans/README.md` has the full status table, every finding
that was *not* turned into a plan, and a "considered and rejected" section so
nothing gets re-audited.

### Plan 010 — done, and what it found

`plans/010-grade-the-picks.md`, all five tasks, each annotated inline with the
corrections the plan needed once executed.

| Task | Landed |
|---|---|
| 1 | `grade_pick` returns `None` for a type it has no branch for; six call sites updated, not the two the plan named |
| 2 | `PickModel.prop_player` / `prop_market` + migration + backfill script |
| 3 | `collectors/espn_box_score.py` — post-game box scores, keyed on final games |
| 4 | Props routed to `grade_prop_pick`, with payouts priced from their own odds |
| 5 | `analysis/prop_calibration.py` — win rate and ROI per confidence tier |

**The chain works end to end.** Proven on a copy: 82 props resolved, box
scores collected for both games carrying them, **75 props graded (48 W / 27
L)**, and the report produced real numbers.

**It cannot grade anything in production yet, and that is not a code problem.**
Every prop in the database belongs to a game still `status='scheduled'` with
NULL scores, so `grade_pending_picks` correctly skips all 82. The numbers below
were obtained by reconstructing the two games' real final scores from ESPN
**on a copy** (home/away orientation verified to match before writing).
Production needs the scheduler to run and mark those games final.

#### First prop measurement (NBA, on a copy, 2026-09-17)

```
  tier   settled   wins  losses   win%     units     roi  reliable
  5         36     27       9    75.0%    +8.62  +0.239    NO
  4         16      8       8    50.0%    -3.66  -0.229    NO
  3         10      7       3    70.0%    +0.42  +0.042    NO
  2         10      5       5    50.0%    -2.19  -0.219    NO
  1          3      1       2    33.3%    -1.07  -0.358    NO
```

**5-star vs 4-star: 75.0% vs 50.0%, 5-star higher by 25 points.** Mildly
encouraging and **not actionable**: all 75 graded props come from **2 games**,
so tier 5's effective n is 19.5 (ICC 0.05) down to 10.1 (ICC 0.15). Every tier
is flagged unreliable, correctly.

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.prop_calibration --db <abs win path> --sport nba
```

**If it prints `REFUSING: no graded prop picks`, that is the tool working** —
an all-zero table would read as "confidence predicts nothing", which is a
finding, not the absence of one.

Of the five "natural next pieces" listed on 2026-09-16, three are done:

1. ~~Reconcile `historical.py`'s post-game Elo convention.~~ **Done** —
   `c292068`. It no longer implements a replay at all; it delegates to
   `team_stats.backfill_elo_history` and keeps only the `EloRating` upsert.
3. ~~Re-run the 007 calibration report.~~ **Done** — numbers below.
5. ~~Wire CI.~~ **Done** — `9ff809e`, green on 3.12 and 3.14.

### Still open

1. **Decide `EloRating`'s fate.** Routed around rather than fixed. Still
   written by `compute_historical_elo` and still read as the pick generator's
   fallback for the 14/239 upcoming NBA games with no replayable history. The
   convention clash that blocked this is resolved, so the decision is now
   unblocked: wire it up properly or delete it.
2. **Decide what to do about the three constant features.** `pace`,
   `offensive_rating` and `defensive_rating` all need possession counts no
   collector supplies. Every Brier number this project has produced came from
   four working features, not seven. Options: source possessions, drop the
   features, or leave them defaulted and stop counting them.
3. **The underdog overconfidence — do not retune on it yet.** See the
   calibration section; the bins carrying the finding are n=15 and n=28, under
   the report's own `min_bin=30`. **More completed games, not a threshold
   change.**
4. **Migrate `PaperTrading.tsx` to React Query** — 009 left a documented
   suppression there naming this as the real fix.
5. **Prepare production and start the pipeline.** See the block at the top of
   this file. Until then nothing grades, no box scores accumulate, and 012's
   Task 4 cannot measure anything.
6. **012 Task 4 needs volume, not code.** Box scores exist for 2 games, so all
   52 players have exactly one `game_log` row and **zero props clear the three
   values Task 3 now requires**. Running the report today returns 0 analysed
   and the calibration tool correctly refuses.
7. **Make `espn_box_score` use `game.espn_id`** before that collection run.
   It still calls `resolve_espn_event`, which searches the scoreboard on three
   dates per game, because it predates the column — and its module docstring
   now asserts something 014 made false ("ESPN shares no id with our Game").
   Using the id turns ~4000 requests into ~1014 for a 1014-game run.
8. **ncaab teams hold display names in `abbreviation`** (`"Pennsylvania
   Quakers"`), so 60 rows can never match ESPN. Needs a team-identity fix.
9. **`EspnStatsSource._find_team_id` does not exist**, so
   `fetch_season_averages` raises `AttributeError` and the season-average
   fallback is dead. P1 in its own right, given `nba_api` cannot reach
   `stats.nba.com` from here.
10. **`MARKET_STAT_MAP` is duplicated** in `grader.py:13` and
    `prop_analyzer.py:12` (as `MARKET_TO_STAT`). A real drift risk.
11. **Guard `migrate_api_usage`'s `DROP TABLE`.** It is the sharpest object in
    the repo and the reason 011 mattered; an explicit opt-in for destructive
    migrations would shrink the blast radius of every future mistake.
5. **Let the scheduler run, then re-measure props.** This is the gate on the
   digest now. The grading chain is built and verified; it needs games
   carrying props to reach `final`. Until then the prop numbers rest on two
   nights of basketball.
6. **Fix the import side effect on `backend.api.main`** — finding 4 above.
   Lazy app construction, or requiring `DATABASE_PATH` with no default, would
   both do it. Needs its own plan; the fix has to keep
   `uvicorn backend.api.main:app` working (`Dockerfile:44`).
7. **`nba_api_source.fetch_recent_games` is broken** and nothing depends on it
   for grading any more. `nba_api_source.py:157` passes `last_n_games=n` to
   `PlayerGameLog`, which has no such parameter, so it raises before any HTTP
   call and the fallback chain swallows it as a warning. Line 167 already
   slices `games[:n]`, so the fix is deleting the kwarg — but
   `stats.nba.com` also read-times-out from this machine, so fixing it buys
   nothing for data. It matters because it silently degrades **pre-game** prop
   analysis: `prop_pipeline.py:80-83` asks for recent form, always gets
   nothing, and logs a source failure rather than a bug. **Props are being
   analysed on season averages alone.**

### Calibration baseline (NBA, measured 2026-09-17)

Out-of-sample, 310 eval games, split 2026-01-31:

| | Brier |
|---|---|
| Out-of-sample | **0.2032** |
| In-sample control (`--in-sample`) | 0.1948 |
| Always predict 0.5 | 0.25 |

The out-of-sample penalty is only **0.0084** — the model is *not* badly
overfit. Its problem is bias, not variance.

The 0.5-0.7 range (45% of games) is now well calibrated: gaps **-0.006** and
**-0.008**, improved from +0.050 / -0.156 before the backfill. That is 008
working.

**Underdog overconfidence survived 008** and is the clearest open modelling
problem: the 0.2-0.3 bin predicts 0.263 and observes 0.067 (**+0.196**);
0.3-0.4 predicts 0.360, observes 0.250 (**+0.110**). Both bins are below
`min_bin` and flagged `NO`. **Directional, not established.**

Effective n is **263 (ICC 0.01) to 164 (ICC 0.05)**, not the raw 310 — games
sharing a team are not independent observations.

Only NBA has the volume (1023 finals). ncaab has 22 and mlb 13, both far under
`MIN_EVAL_GAMES = 200`.

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba --db 'C:\Users\mwill\Documents\mwilliams2733\sports_picks\sports_picks.db'
```

`--db` needs a **Windows** path; a git-bash `/c/...` path fails to open.
**If it ever reports 0.1836 again, the features are missing, not good** — that
is the pre-backfill number.

## Outstanding operator actions

1. ~~**Rotate the Odds API key.**~~ **DONE, fully closed 2026-09-17.** A live
   key had sat in plaintext in `uvicorn.log`. The key was rotated, `uvicorn.log`
   was truncated to 0 bytes, and the public git history was checked: the only
   `apiKey=` matches in tracked files are the redaction regex in
   `odds_api.py`, the `FAKEKEY123` test fixture, and an `abc1…` placeholder in
   plan 005. Nothing real was ever committed.

   **If you rotate again:** the key must reach **both** places the deployment
   reads it — `~/.secrets/shared.env` locally and `/opt/sports-picks/.env` on
   the server (`deploy/setup.sh:51`). Updating only the local one leaves the
   deployed copy failing on its next odds fetch.

   Note the repo is **public** (`github.com/mwilliams2733/sports_picks`).
2. **Do not set `digest.enabled: true` yet.** Still the recommendation, but
   the reason has moved twice and is now much narrower.

   Everything that *was* blocking it is done. The 007 report has been re-run
   against the backfilled production data. The dry-run preview has been run
   and it works — real matchups, real odds, real rationales, and the pre-008
   failure mode is gone (that preview led with five heavy favourites from
   −286 to −2336; the current one leads with a −110 at three stars). Props
   are now gradeable and measurable.

   **What blocks it now is sample size, not machinery.** The digest is
   majority props by row count, and every prop measurement rests on **two
   games**: 5-star beats 4-star by 25 points, which is encouraging, with an
   effective n of 10-20. The game model is separately still overconfident on
   underdogs by 11–20 points on n=15 and n=28 bins, and three of its features
   are constant.

   **The gate is: let the scheduler run, let props accumulate across dozens
   of games, then re-run `prop_calibration` and the 007 report.** The data is
   honest, the tools are built and verified, and the evidence is two nights
   deep.
3. The digest's dry-run preview writes `digest_preview.html` to the repo root;
   it is git-ignored.

### The dry-run preview was run on 2026-09-17 — and found the real blocker

The preview works. For 2026-05-26 it rendered one game pick (Over 217.1,
−110, ★★★☆☆, +7.3%) and five player props, with real matchups, odds and
rationales. The pre-008 failure mode is **gone**: that preview led with five
heavy favourites from −286 to −2336; this one leads with a −110 at three
stars. Run for *today* it correctly produced nothing, there being no games
dated 2026-09-17.

Nothing was sent. Four independent guards: `digest.enabled: false`, the
`dry_run_path` early return in `sender.send_email`, no `RESEND_API_KEY`, and
empty `recipients`. `digest.selector` is pure-read, so production was not
written to.

**But five of the preview's six rows are props, and props have never been
graded — nor can they be.** `pick_results` has **0 rows**. Three defects sit
behind that, and they compound:

1. **`grade_pick` grades every prop as a loss.** It has branches for
   moneyline, spread and over_under, then `else: return "loss", -1.0`
   (`grader.py:62-63`). `pick_type="prop"` hits the else.
   `scheduler.grade_pending_picks` routes *every* ungraded pick through it.
   This is **latent, not active** — `pick_results` is empty because the
   scheduler has not run, not because the code is safe. The next
   `morning_scout` marks all 82 props as losses.
2. **`PickModel` has no `prop_player` or `prop_market` columns.** `PaperPick`
   has both, which is why the PaperPick loop 30 lines below
   (`scheduler.py:290`) branches correctly to `grade_prop_pick` while the
   strategy-pick loop cannot. The obvious fix — copy that branch — has nothing
   to pass.
3. **`player_stats` holds 0 rows with `stat_type="game_log"`.**
   `grade_prop_pick` looks up exactly that, so even with the schema fixed it
   returns `None` for every prop. There is no outcome data to grade against.

The same file contains the correct pattern and the broken one, thirty lines
apart. That is the dead-wiring shape again: `grade_prop_pick` is correct, is
tested, and is simply never reached from the strategy path.

**Consequence for the digest decision.** Everything measured on 2026-09-17 —
Brier 0.2032, the bin table, the underdog finding — describes the **game**
model. The digest is majority props by row count, and prop confidence has
never been checked against a single outcome (39 of 82 props, 48%, sit at
maximum confidence). Keep `digest.enabled: false`: not because the digest is
broken, but because its dominant content is unvalidated and currently
ungradeable.

Planned as **`plans/010-grade-the-picks.md`**. **Task 1 is done**
(`6117d5e`): `grade_pick` now returns `None` for a type it has no branch for,
and all six call sites treat that as "leave ungraded". The latent corruption is
closed — props are correctly ungraded rather than wrongly graded.

**Task 3's spike is done too, and the answer is good: ESPN, free, no purchase
needed.** All three NBA sources currently fail, but only one of the three for a
reason that costs money:

- `NbaApiSource` passes `last_n_games` to `PlayerGameLog`, which has no such
  parameter — a `TypeError` thrown *before* any HTTP call, which masked
  everything else. Remove it and the real problem appears: `stats.nba.com`
  read-times-out from this machine (30s, 60s, 90s all failed). Unusable here.
- `BallDontLie` returns 401; it needs a paid key.
- `ESPN` 404s because the code asks `site.api.../nba/athletes`, a path that
  does not exist. The scoreboard and summary endpoints both return 200, and
  `summary?event=<id>` carries full player box scores — MIN, PTS, REB, AST,
  3PT, STL, BLK, TO — covering every NBA market in `MARKET_STAT_MAP`.

Two traps recorded in the plan. There is **no shared game id** (`Game` has no
`espn_id`), so events must be matched on date plus team abbreviations. And
**our dates run one day ahead of ESPN's** (UTC vs ET): of 8 sampled final NBA
games, 7 matched at offset −1 and 1 matched exactly, 0 missing. Matching on
exact date alone finds about 1 in 8 and looks like missing data rather than a
timezone bug.

## Scratch that did not survive

Under the session temp dir, now gone: the SDD ledger for the digest plan, the
per-task briefs and review packages, `sports_picks.backup.db` and
`sports_picks.work008.db` (DB copies), and the review diffs. None of it is
needed — the git history and `plans/` are the record.

`.superpowers/` in the repo holds the execution reports that were written:
`004-activity-feed-report.md`, `007-calibration-report.md`,
`008-team-stats-report.md` and `009-frontend-eslint-report.md`. It is
git-ignored, so those survive on disk but are not in history.

From the 2026-09-17 session, still on disk and worth keeping until you are
satisfied with live behaviour:

- `sports_picks.backup-20260916-231622.db` — the pre-backfill production
  database, git-ignored, verified `integrity_check: ok` with all 708 picks
  before anything was written.

Gone with the session temp dir, and not needed: the plan-010 working copies
(`props.db`, `box.db`, `chain.db`) and the scripts that drove them. Everything
they proved is in the commits and in `plans/010-grade-the-picks.md`. Note the
**production database was never written to by plan 010** beyond the two
columns a stray import migrated in (finding 4 above) — all 82 props are still
ungraded there, with `prop_player` and `prop_market` NULL.

## Your database — backfilled 2026-09-16, still ungraded

During plan 008 all analysis ran against copies and production was left empty.
It has now been backfilled deliberately:

| | before | after |
|---|---|---|
| `team_stats` distinct game_ids | 1 | **1058** |
| `elo_history` rows | 0 | **2116** |
| `picks` count / max id | 708 / 708 | **708 / 708** unchanged |
| `games` | 1664 | **1664** unchanged |
| `integrity_check` | ok | **ok** |

Backup taken first: `sports_picks.backup-20260916-231622.db` in the repo root,
git-ignored, verified `integrity_check: ok` with all 708 picks. Keep it until
you are satisfied with live behaviour.

`pace` / `offensive_rating` / `defensive_rating` are still present for exactly
**1** game — structurally refused, not fabricated.

**This changes live pick generation.** The model now trains on real
point-in-time features instead of a matrix that was almost entirely zeros, so
picks generated from here differ from those generated before. The calibration
report against production reproduces the copy exactly: **Brier 0.2032**
out-of-sample, 0.1948 in-sample control.

The daily pipeline keeps both tables current from here
(`update_team_stats_for_games` and `backfill_elo_history` in
`full_pipeline.py:234`), so this was a one-off.

### Current state after plan 010

| | value |
|---|---|
| `pick_results` | **0 rows** — nothing has ever been graded in production |
| props | 82, all with `prop_player` / `prop_market` **NULL** |
| games carrying props | 2, both still `status='scheduled'` with NULL scores |
| `player_stats` `stat_type='game_log'` | **0 rows** |
| `picks.prop_player` column | present, added by a stray import (finding 4) |

Plan 010 wrote nothing to production. Everything it proved ran against copies.

> **Superseded.** The ordered steps below covered plan 010 only. Plans 013 and
> 014 added more, and the authoritative list is now
> **"READ THIS BEFORE STARTING THE PIPELINE"** at the top of this file. Follow
> that one; these three are a subset of it.

To grade in production, in order (subset — see above):

1. Let the scheduler run so the two games reach `status='final'` with scores —
   or, if you want it now, `morning_scout` does box-score collection and
   grading in one pass.
2. `python -m backend.scripts.backfill_prop_fields --db <abs win path>` to fill
   `prop_player` / `prop_market` for the 82 existing props. New props carry
   them at generation time.
3. `python -m backend.analysis.prop_calibration --db <abs win path> --sport nba`

**Back it up first, and dry-run step 2.** Both were verified against copies
(82/82 resolved, resumable on re-run), but the rule stands.

## Historical note: state during plan 008

`sports_picks.db` had **708 picks** (max id 708). Eighteen test picks were
generated during a digest preview and deleted afterwards, with a
blast-radius check confirming no `pick_results` referenced them. All analysis
work ran against copies.

## Plan 016 landed: a home baseline per sport — 2026-09-19

`CalibratedModel` fitted one logistic regression over every sport with no
home feature, so home advantage lived entirely in a single intercept fitted
on a pool that is 88% nba. A one-hot sport encoding now carries it. Both
`train_from_db` and `predict_home_win_prob` build their row through
`build_feature_row`, so the two cannot drift.

### Fitted baseline against the real rate (1418 final games)

| sport | n | actual | before | after |
|---|---|---|---|---|
| nba | 1248 | 0.554 | 0.559 | 0.562 |
| mlb | 93 | 0.505 | 0.559 | 0.511 |
| ncaab | 72 | 0.708 | 0.559 | 0.702 |
| ncaaf | 4 | 0.750 | 0.559 | 0.689 |
| nfl | 1 | 1.000 | 0.559 | 0.706 |

"Before" is the pooled rate, which every sport received. ncaaf (n=4) and nfl
(n=1) are correctly shrunk hard toward the pool by L2 — that is the sample
being thin, not a defect.

### Brier, out-of-sample

| | before | after | effective n |
|---|---|---|---|
| ncaab | 0.2116 | 0.2021 | ~36 (ICC 0.01–0.05) |
| nba | 0.1768 | 0.1768 | 171–303 |

**nba did not regress**, identical to four decimals. nba is the majority of
the training pool, so a gain for ncaab bought at nba's expense would have
been a bad trade; there is no trade.

### Read ncaab's 0.708 with care — it is not home advantage

Every ncaab final game in the database falls between **2026-03-14 and
2026-03-22**: 8 distinct dates, with n=6 on Mar 17–18, n=39 on Mar 19–20 and
n=18 on Mar 21–22. That is the First Four, Round 1 and Round 2 — **neutral
sites**, where "home team" is a bracket seed designation and not a host.

So the 0.708 is higher seeds beating lower seeds, with actual home advantage
near zero by construction. Split temporally it is 0.571 before 2026-03-20 and
0.838 after — the bracket tightening, not a sport constant. The new report
line shows the fit-window figure, 0.5714 over 35 games.

Consequences:

- The mechanism fix stands on its own. One intercept for seven sports is
  wrong regardless, and mlb — genuine regular-season games — moved from the
  pooled 0.559 to 0.511 against its actual 0.505.
- **When ncaab regular-season games arrive in November a ~0.70 baseline will
  be too high** (real ncaab home advantage runs nearer 0.60–0.65), and these
  72 tournament games will be a large share of the ncaab pool during exactly
  the early weeks when picks start flowing. Worth a neutral-site flag on
  `games`, or excluding tournament dates from training. The table has no such
  column today.
- The ncaab underconfidence signature survives the fix: observed still
  exceeds predicted in every populated bin. The report fits on the pre-split
  window where the rate is 0.571 and is then scored against 0.838, so it
  cannot learn what it is being marked on.

### This changes future pick generation only

The 348 graded ncaab picks and their −73.33 units are unaffected, and
re-measuring ROI will not show an improvement until new picks are generated
and settled. Do not look for one before then.

`min_edge` stays where it is until that happens. The edge estimate was
inverted above ~10%, so tuning the threshold against inverted edges optimises
the wrong thing.

## Neutral-site flag added and backfilled — 2026-09-19

`games.neutral_site`, populated from ESPN's `competitions[0].neutralSite`.
Verified against the live API before building anything: ncaab 2026-03-19
returned 16/16 True, a regular-season ncaab date returned False, and an nba
regular-season date returned 8 False / 1 True — so it discriminates, and it
is not a college-only concern.

Backfill result against production (backed up first, `integrity_check: ok`,
1823 games / 708 picks unchanged):

| sport | changed | ->neutral | correct | no_match |
|---|---|---|---|---|
| nba | 6 | 6 | 1246 | 0 |
| ncaab | 73 | 73 | 1 | 0 |
| ncaaf | 2 | 2 | 73 | 0 |
| mlb | 0 | 0 | 112 | 0 |

**71 of 72 ncaab final games are neutral-site.** Exactly one hosted ncaab
game exists in the entire database.

The first pass left 772 nba rows unmatched — the UTC/Eastern date convention
again, the same problem `backfill_espn_ids` carries neighbour-date logic for.
Added the same: neighbours are consulted only for rows the exact date missed,
so the common case stays at one request per date. `no_match` went to 0.

### How the flag reaches the model

A sport's one-hot slot means "home advantage for this sport is in effect", so
it does not fire at a neutral venue. Gating alone proved insufficient: with
the slots gated, a neutral game and a sport absent from `SPORT_VOCAB` both
encode as all-zeros, so neutral outcomes fitted the bare intercept and every
slot-less sport inherited them. On a fixture with hosted nba at 0.55 and
all-neutral ncaab at 0.75, a *hosted* ncaab game came back 0.740 — the seed
effect was not removed, only promoted. `build_feature_row` therefore also
appends an explicit neutral slot; the same fixture then gives 0.655.

### Residual, worth knowing before November

Production now predicts **0.693 for a neutral game of any sport** with flat
features. That is the neutral slot having absorbed the ncaab bracket effect:
the designated home side is the higher seed and wins ~71%. It should have
been carried by `elo_diff`, but ncaab Elo is nearly uniform — the sport has
only 8 days of games, so ratings have barely moved.

So the seed effect is not gone; it is confined. It no longer touches ncaab's
home-court baseline (what November needs), nor nba's or mlb's. But it does
mean neutral-site predictions carry a ~+0.19 bump that is really seed
strength, which is wrong for the 6 neutral nba games where no seeding exists.
Expect it to shrink on its own once ncaab Elo becomes informative and
`elo_diff` can explain the seeding. Worth re-measuring then rather than
tuning now.

`ncaab` hosted now reads 0.716 off a single hosted game plus the shared
intercept. The number barely moved from the old 0.702, but its provenance
changed completely: it is now "almost no information" rather than "71 neutral
games said 0.71". There is no data to move it toward until November.

## Picks are flowing again — 2026-09-19 afternoon

**824 picks, newest today.** The previous newest was 2026-05-24; the drought
was nearly four months. Today's run produced 116 ncaaf picks: 41 over/under,
38 spread, 37 moneyline.

### What was actually stopping it

The running scheduler was **stale**. It started at 08:22 and every fix from
that morning was committed after: `0131318` at 08:29, `ae412df` at 09:22.
Its last real run logged ~17 repetitions of `'EspnStatsSource' object has no
attribute '_find_team_id'` — the bug `ae412df` fixed — and ended with
`{'games': 86, 'stats_fetched': 0, 'picks_generated': 0}`. The 08:22 restart
also landed just after the 08:00 window, so nothing had run on new code at
all. **Restarting the scheduler is part of shipping a fix here**; the code
being committed and green does not put it in production.

### The Odds API key was being written to scheduler.log

httpx logs the full request URL at INFO and the Odds API takes its key as a
query parameter, so every odds fetch wrote it in plaintext. `redact_api_key`
only ever guarded exception strings. Fixed in `7c50ffb` with a logging filter
installed from `OddsAPICollector.__init__` — the one place guaranteed to run
before httpx can log a URL carrying the key.

The existing log files have been scrubbed and `*.log` is now gitignored.
**The key that appeared in them should still be rotated**: it sat in
plaintext on disk, and scrubbing the file afterwards does not undo that.

### ncaaf: two separate causes, both needed

`refresh_team_tables` asked ESPN for `?limit=500` and took what came back.
ESPN caps the page there and exposes no page count, so college football —
762 teams — was silently truncated. The snapshot is now paged: ncaaf 500 →
760, every other sport byte-identical.

Five schools also needed aliases, because the Odds API and ESPN genuinely
disagree: UMass/Massachusetts, Southeastern Louisiana/SE Louisiana,
Appalachian State/App State, Sam Houston State/Sam Houston, Southern
Mississippi/Southern Miss. Pagination alone would not have fixed them, and
aliases alone would have pointed at names the truncated snapshot lacked.

### mlb's missing odds were not a collector bug

`scheduled_sports` is `['nfl', 'ncaaf', 'mlb']`. `morning_scout` ran the
already-due ncaaf window **inline**, that pipeline spent over 25 minutes
fetching player stats one athlete at a time, and the loop never reached mlb.
14 scheduled mlb games went the day with zero odds and nothing logged an
error — the loop was simply still inside the earlier call.

Due windows are now collected and run after every sport is scheduled, still
serialised. A failure in one no longer abandons the rest.

### ESPN throttling is real

The forced scout made **3172 requests and collected 128 403/429 responses**.
ESPN publishes no rate limit and sends no `Retry-After`, but it does start
refusing. `espn_http.get_with_retry` is now the single policy for both
collectors: 429/403/5xx only (a 404 is a real answer), full jitter, bounded
attempts.

### Still open

- **Rotate the Odds API key.**
- The player-stats collector is sequential, one request per athlete. Retry
  makes it more correct, not faster; a ncaaf slate is still ~25 minutes.
- `ubuntu-latest` moves to Ubuntu 26 on 2026-10-19. Left unpinned.
- Three dead model features (`offensive_rating`, `defensive_rating`, `pace`)
  still need possession counts no collector supplies.
- November: re-measure the neutral slot's 0.693 and ncaab's hosted baseline.

## fetch_odds_now: the perishable half of a window — 2026-09-19 evening

`_run_window` does two jobs back to back that share nothing but a sport.
Storing odds takes ~10 seconds and is **perishable** — prices move. Collecting
player stats takes 25+ minutes and thousands of ESPN requests and is not
time-sensitive at all. Because they are welded together and due windows run
serially, an mlb odds fetch sat behind five ncaaf stats collections.

`backend/scripts/fetch_odds_now.py` runs only the perishable half. It
delegates to the same `fetch_and_store_odds` / `generate_and_store_picks` /
pitcher remap that `_run_window` calls, so the two cannot drift. Props are
opt-in via `--props`.

    python -m backend.scripts.fetch_odds_now --db <abs path> --sport mlb

Measured against production: **19.7 seconds**, 126 game picks, 19 credits.

### Two things it surfaced

**Only 5 of 15 mlb games matched an odds event.** 49 rows across 5 games and
11 books landed for today; the other 138 of the 187 stored belong to later
dates. It is *not* a started-game filter — 1815 (HOU/ATL) and 1816 (STL/WSH)
had not started and got nothing, while 1812–1814 had started and did:

| matched | not matched |
|---|---|
| CIN/CHC, PIT/KC, TEX/TOR, COL/SEA, LAD/SF | HOU/ATL, STL/WSH, ARI/NYY, SD/MIA, LAA/MIN, and 5 more |

That pattern looks like the odds-to-fixture matcher failing on a subset, the
same class of problem as the ncaaf name mismatches, but it has **not** been
diagnosed. Instrument `fetch_and_store_odds` before concluding anything —
no "Cannot identify mlb ..." warning was logged, which is itself a clue.

**`fetch_pitcher_scores_for_date` returned nothing.** All 12 mlb games logged
`no score for game N (key=..., available=[])`, so every MLB pick today is
priced on a neutral starter. The guard worked — picks were still generated —
but the pitcher signal is absent, not merely degraded.

## The MLB pitcher fetch was returning nothing — 2026-09-19

`fetch_pitcher_scores_for_date` returned `{}` every day, so every MLB pick
was priced on a neutral 0.5 starter — the dominant MLB feature, silently off.

`MLBStatsCollector.fetch_schedule` read `team["abbreviation"]`. The MLB Stats
API only sends that key when the request hydrates `team`; with
`hydrate=probablePitcher` the object is `{id, link, name}`. So `.get()`
returned `None` for all 15 games, the caller skipped each one for want of a
key, and nothing logged anything.

### The fix resolves the name instead, and does real work doing it

`name` is already in the payload — no extra hydration needed. It goes through
`team_identity.resolution_of("mlb", name)`, which also handles a second
problem: **MLB's own abbreviations disagree with ESPN's**, and our `teams`
table holds ESPN's. MLB says `AZ` and `CWS` where ESPN says `ARI` and `CHW`.
All 30 MLB team names resolve `exact` against the snapshot, so there is no
hand-kept mapping to rot and a rename is picked up by refreshing the snapshot.

Verified against the live API:

```
pitcher scores returned: 15 games (was 0)
games where BOTH pitchers are neutral 0.5: 0 of 15
games mapped to a Game.id: 15 (was 0)
```

### The test was certifying the bug

`test_run_mlb_window_fetches_pitcher_scores` built its payload as
`{"team": {"id": 111, "abbreviation": "BOS"}}` — an `abbreviation` the
endpoint never sends, and no `name`. It passed for months against code that
read exactly that key. The fixture now carries `name` and no `abbreviation`,
with a comment saying not to add it back. Same failure mode as the two legacy
ESPN parser tests removed earlier: a hand-built fixture asserting the
implementation's assumption rather than the API's reality.

A further test pins the opposite direction — if the request ever hydrates
`team`, MLB's `AZ` must still resolve to `ARI` rather than be trusted.

### Two smaller things fixed alongside

- An empty pitcher map now logs how many games were skipped and why. Before,
  `{}` was indistinguishable from "no MLB games today".
- Team identity is checked *before* the two pitcher stat requests. It used to
  fetch both pitchers' season logs and then discard the game — 30 wasted
  calls a day while the abbreviations were all `None`.

## Odds were being attached to the wrong game — 2026-09-19

`_find_game_by_teams` ordered candidates `by date desc` and took the first,
ignoring the event's `commence_time` entirely — a field the collector has
always returned and `_store_odds` never passed. In a series (the same two
teams on consecutive days, routine in baseball) **tonight's prices were
written onto tomorrow's fixture**.

Two harms, not one: tonight's game cannot be priced, and tomorrow's game
carries a price that is not its own. Unmatched events were skipped with a
bare `continue`, so none of it logged.

### Measured

| | before | after |
|---|---|---|
| mlb 09-19 | 5 / 15 | **12 / 15** |
| mlb 09-20 | 9 / 9 (wrong prices) | 9 / 9 (its own) |
| ncaaf 09-19 | 64 / 73 | **70 / 73** |

The 3 remaining mlb games are not in the API's event list at all — already
underway. The 3 ncaaf are the same.

### Three separate defects, found in sequence

1. **commence_time ignored.** Fixed by matching the nearest candidate within
   a window: 12h when the game has a `start_time`, 1 day when only its date
   is known. Outside the window nothing matches — no price beats another
   fixture's price.

2. **My first fix had a tie bug.** Games created from the Odds API have no
   `start_time`; those from ESPN do. A late game's UTC timestamp falls on the
   *next* calendar day, so a date-only candidate dated that day scored a
   perfect zero and tied the game actually starting at that instant — and
   `date desc` handed the tie to the guess. Three mlb events still matched
   the wrong fixture. The rank is now `(has_start_time, distance)`, so a
   precise match outranks a guess rather than tying it.

3. **The lookup used raw equality while game creation used `team_identity`.**
   The two could disagree about the same label: creation resolves "Sam
   Houston State Bearkats" through an alias, raw equality against ESPN's
   "Sam Houston Bearkats" does not. `_lookup_team` now falls back to the
   resolver, which recovered 5 ncaaf games immediately.

With the new warning in place the last ncaaf failure named itself —
`'Nicholls State Colonels'` — and is now an alias. ESPN separately lists
"Nichols Bison" (one L, a different school), so this could not be a fuzzy
rule: the two resolve to NICH and NICC respectively.

`_store_odds`'s return value counts bookmaker rows, not events. The log line
calling them "events" has always been wrong; the docstring now says so.

## Regenerating today's mlb picks — 2026-09-19

Asked to redo them with the pitcher scores. Three things came out of it, and
the headline is not the one expected.

### The active strategy does not use pitcher scores

`strategies` has `ensemble` active for games. `EnsembleStrategy` contains no
reference to `pitcher_skill_score`, and its `FACTOR_CODES` omits
`pitcher_edge`, so `Strategy._factors` filters the factor out. Only
`SportSpecificStrategy` consumes it.

So the pitcher fix is correct and the data now flows, but **under the active
strategy it changes nothing about MLB output**. None of the regenerated picks
cite a pitcher factor, and that absence is honest — `strategy.py:124` says a
factor the model did not use must never appear in a rationale.

Making MLB actually use pitcher skill is a strategy question, not a collector
one: either switch the MLB path to `SportSpecificStrategy` or add the term to
the ensemble. Not done here.

### Picks were being made on games already underway

`generate_and_store_picks` filtered `status == 'scheduled'`, which lags by
hours because ESPN updates it late, while the odds feed switches to in-play
prices the moment a game starts. Generating at 23:57 UTC produced a **+3300
moneyline on a game that began at 20:10** — a price nobody could take, on a
result already half-decided, which would then have been graded as a real
wager. 20 of 34 picks that run were on underway games; all 20 deleted.

`skip_started=True` is now the default, with an opt-out for backtests, which
deliberately pick games long over.

### The generator was not idempotent

It always inserted. Every window run re-picked every scheduled game, so the
day carried one copy per run: **354 picks, 221 of them redundant**. Ungraded
that is noise; graded it is the same wager counted three times in ROI. Picks
are now skipped per `(game, strategy, pick_type)`.

Left alone rather than updated, because `odds_at_pick` is the price the bet
was taken at and ROI is measured against it. To genuinely redo a pick, delete
the row and run again — which is what this exercise did.

A sport filter was added at the same time: `fetch_odds_now --sport mlb` was
re-picking every other sport as a side effect.

### Result

14 mlb picks for 2026-09-19, all on games not yet started, prices between
+125 and +324 on moneylines and −110 on spreads and totals. Down from 34,
of which 20 were unplaceable.

**The 221 duplicate ncaaf picks from earlier today are still in the table.**
They are ungraded. Deleting the redundant copies (keeping the earliest of
each group) is a separate cleanup and has not been done.

### Worth a look

Every regenerated `over_under` pick carries `edge_pct = 50.0` exactly. A 50%
edge on a total priced at −110 is not plausible; it looks like the totals
path is not computing an edge at all. Not investigated.

## Duplicate picks cleaned, and what they did to ROI — 2026-09-19

`generate_and_store_picks` inserted unconditionally until today, and every
window run re-picked every scheduled game. The table carried one copy per
run: **622 redundant rows out of 1062**.

`backend/scripts/dedupe_picks.py` removes them. It keeps the lowest id in
each `(game, strategy, pick_type, prop_player, prop_market)` group — the pick
as first made, matching the idempotence rule the generator now follows. Prop
player and market are part of the key because one game carries many prop
picks; keying on `pick_type` alone calls them all duplicates of each other.

**A dry run is the default and `--apply` is required.** This deletes, unlike
the backfill scripts where the flag runs the other way.

### Applied to ncaaf 2026-09-19

112 groups, **221 rows deleted**, none graded. `pick_results` untouched at
519, no orphans, `integrity_check: ok`. That date now has 119 picks and zero
duplicate groups.

### The ROI numbers in this file were measured over a duplicated table

| | n | units |
|---|---|---|
| all graded picks, as stored | 519 | **−46.55** |
| of which redundant copies | 316 | −32.00 |
| **first copy only — the real book** | **203** | **−14.55** |

The same wager was graded repeatedly. nba game 1018's Under carries seven
`pick_results` rows at a payout of 0.909 each, so one bet contributed +6.36
units. Every ROI, units and win-rate figure quoted earlier in this file —
including the moneyline analysis and the −73.33 ncaab units — was computed
over this table and is inflated by however many times each pick happened to
be duplicated. **Re-measure before drawing any conclusion from them.**

The copies are not even consistent with each other. ncaab game 1260's
moneyline was "AWAY ML at +367" on the first run and "HOME ML at +150" on
the six that followed; nba 1018's total went "Under 226.2", "Under 219.7",
then "Over 73.3" five times. So a duplicate is not a re-record of one
decision — it is a different decision made later, against a moved line.

### Still open: 316 graded duplicates

The script refuses them by design. Deleting a graded row rewrites a recorded
result, and deciding what a graded duplicate should have been is a judgement
it has no business making. A further 85 ungraded duplicates remain on other
sports and dates and can be removed whenever wanted:

    python -m backend.scripts.dedupe_picks --db <abs path> --apply

Removing the graded ones needs a decision first: keep the first pick and drop
its later re-prices (consistent with the generator), or treat each re-price
as a separate wager (which is what the table currently asserts).

## The book, re-measured after deduplication — 2026-09-19

Decision taken: keep the first pick of each market, drop the later
re-prices, graded or not. That makes the book what it would have been had
the generator always been idempotent.

    python -m backend.scripts.dedupe_picks --db <abs path> --include-graded --apply

401 rows deleted (316 graded, 85 ungraded) with their `pick_results`.
`integrity_check: ok`, `foreign_key_check` clean, 0 orphaned results, 0
duplicate groups remaining.

| | before | after |
|---|---|---|
| picks | 841 | 440 |
| pick_results | 519 | **203** |
| units | −46.55 | **−14.55** |

### The corrected book

| sport | type | n | win% | units | roi |
|---|---|---|---|---|---|
| nba | moneyline | 19 | 36.8% | +7.00 | **+0.368** |
| nba | over_under | 15 | 53.3% | +0.27 | +0.018 |
| nba | prop | 50 | 54.0% | −3.10 | −0.062 |
| nba | spread | 12 | 50.0% | −0.55 | −0.046 |
| ncaab | moneyline | 35 | 25.7% | −14.91 | **−0.426** |
| ncaab | over_under | 37 | 54.1% | +1.18 | +0.032 |
| ncaab | spread | 35 | 45.7% | −4.45 | −0.127 |
| **TOTAL** | | **203** | **45.8%** | **−14.55** | **−0.072** |

### What survives from the earlier moneyline analysis, and what does not

**Survives, and is the whole story:** ncaab moneyline is −0.426 ROI on 35
picks while nba moneyline is +0.368 on 19. The direction and the split by
sport are unchanged, which is what plan 016 was built on. The pooled
home-advantage intercept remains the best explanation.

**Does not survive:** every magnitude. The earlier figures — "142 graded
moneyline picks", "−73.33 units", the edge-bucket table showing 0-for-36 —
were counted over duplicated rows. There are **54 graded moneyline picks in
total**, not 142. Any bucketed breakdown of them needs redoing from scratch;
with n=35 for ncaab the buckets will be too thin to carry much.

**State the effective sample honestly from here.** 203 graded picks across
roughly 100 games, many sharing teams, is not 203 independent observations.

## Edge buckets, redone on the deduplicated table — 2026-09-19

`backend/analysis/edge_buckets.py`, so these figures can be re-derived. The
first version of this analysis was run by hand, reported "142 graded
moneyline picks" and "0-for-36", and turned out to have been computed over
duplicate rows.

    python -m backend.analysis.edge_buckets --db <abs path>
    python -m backend.analysis.edge_buckets --db <abs path> --sport ncaab
    python -m backend.analysis.edge_buckets --db <abs path> --min-price 150 --max-price 600

### All moneyline, n = 54

| edge | n | win% | avg price | units | roi | away | neutral |
|---|---|---|---|---|---|---|---|
| 0-20% | 32 | 43.8% | +128 | +4.14 | **+0.129** | 21 | 17 |
| 20-35% | 12 | 16.7% | +375 | −2.05 | −0.171 | 11 | 9 |
| 35%+ | 10 | **0.0%** | +879 | −10.00 | **−1.000** | 10 | 9 |

Aggregated: edge <20% is **+0.129 ROI on 32 picks**; edge ≥20% is **−0.548 on
22**. The inversion the earlier analysis claimed is real and survives the
correction. Its magnitudes do not: 54 graded moneyline picks exist in total,
not 142.

### The mechanism, stated exactly

Every ncaab moneyline pick with a claimed edge of 20% or more — **18 of
them — was an AWAY pick at a NEUTRAL venue, and all 18 lost.**

They are NCAA tournament Round 1 and 2 games on 2026-03-19, -20 and -21,
where "away" is a bracket seed designation, not a road team. So the model
claimed 21-42% edge on lower seeds and went 0-for-18, with the claimed edge
rising alongside the price: +235 at 21% edge through +1250 at 42%.

That is not a generic "the edge estimate is inverted". It is the missing
host term, seen from the other side: the model priced these games with a
pooled ~0.56 home advantage that did not exist, underrating the
bracket-home (higher-seeded) side and manufacturing edge on the lower seed
in exact proportion to how big an underdog it was.

### How strong is this actually

Price-implied breakeven across those 18 is 16.3%, so P(0 wins) under
independence is **0.041**. But they span **three dates**, all neutral-site
tournament basketball. Eighteen picks from three days is not eighteen
independent trials; taking the day as the unit leaves three. Suggestive,
not established.

Holding price roughly constant (+150..+600) leaves too little to separate
edge from price: 0-20% is −0.091 on n=10 against 20-35% at −0.171 on n=12,
which is noise, and the 35%+ bucket has n=1.

**Do not tune `min_edge` on this.** The right correction is the one already
made — the sport one-hot and the neutral-site gate.

### What this says about the neutral slot's 0.693

Previously flagged as a residual concern. On this evidence it is doing the
right job for the population it was fitted on: bracket games, where the
designated home side really does win about 70%. Applied to those 18 games it
would have favoured the higher seed and declined the bet. It remains wrong
for a neutral game with no seeding — the 6 nba ones — which is a narrower
problem than first described.

## Spread and over_under buckets — 2026-09-19

    python -m backend.analysis.edge_buckets --db <abs path> --pick-type spread
    python -m backend.analysis.edge_buckets --db <abs path> --pick-type over_under

### Spread: the cleanest evidence of the inversion, n = 47

Every spread pick is priced at **−110**, so the price confound that muddies
the moneyline buckets is absent by construction. This is a like-for-like
comparison in a way the moneyline table is not.

| edge | n | win% | units | roi | away | neutral |
|---|---|---|---|---|---|---|
| 0-20% | 23 | 60.9% | +3.73 | **+0.162** | 17 | 13 |
| 20-35% | 10 | 30.0% | −4.27 | −0.427 | 10 | 8 |
| 35%+ | 14 | 35.7% | −4.45 | −0.318 | 14 | 14 |

Aggregated: **edge <20% wins 60.9% (+0.162 ROI); edge ≥20% wins 33.3%
(−0.364)**, against a breakeven of 52.4% at −110.

Same fingerprint as the moneyline: **all 24 high-edge picks are AWAY picks,
22 of 24 at neutral venues, 22 of 24 ncaab.** At a constant price this
cannot be a big-underdog artifact — the model is taking the wrong side.

P(≤8 wins of 24 | breakeven, independent) = **0.048**. The low bucket's
14-of-23 is unremarkable on its own (p = 0.27); the signal is in the high
bucket underperforming, not the low one outperforming. And the 24 span five
dates, mostly the same tournament weekend, so they are not 24 independent
trials.

### over_under: the buckets cannot be read, because the edge is broken

| edge | n | win% | units | roi |
|---|---|---|---|---|
| 0-20% | 2 | 100.0% | +1.82 | +0.909 |
| 20-35% | 0 | — | — | — |
| 35%+ | 50 | 52.0% | −0.36 | −0.007 |

Fifty of 52 graded picks fall in one bucket, which is not a distribution.
Looking at the column directly: **113 of the 147 over_under picks in the
table carry `edge_pct` of exactly 50.0**, and eight graded ones carry an edge
**above 100%** — 130.2%, 128.8%, 124.3% and so on.

An edge above 100 percentage points is not a possible probability edge. On a
two-way market at −110 the largest sensible value is about 47.6%. So
`edge_pct` for totals is not measuring what its name says, and bucketing by
it is meaningless until that is fixed.

What the results do say, independent of the edge column: 52.0% win rate over
50 picks at −110 is a coin flip bleeding the vig (−0.007 ROI). The totals
model has no demonstrated skill either way on this sample.

**Not investigated.** It is a separate defect from the home-advantage work
and affects `over_under` across every sport — 361 picks in the table before
deduplication, the largest single market.

## The over_under "edge" was a constant, not a calculation — 2026-09-19

The arithmetic was never wrong. `edge = (over_prob - 0.5) * 100` is internally
consistent, and at −110 on both sides the vig-free fair probability really is
0.5. The inputs were the problem.

### `_predicted_total` returns 200.0 for every game in every sport

```
home_pts = pace * (own_off + opp_def) / 200
```

`offensive_rating`, `defensive_rating` and `pace` are the three dead features
the calibration report has named on every run for months. `team_stats` holds
only `rest_days`, `point_diff` and win/loss splits — none of the three is ever
written — so all fall back to 100.0 and the total is `100 × 200/200 × 2 = 200`.

Against real market lines that single constant decides the side:

| sport | market totals | model | always picks |
|---|---|---|---|
| mlb | 6.5 – 20.5 | 200 | Over |
| ncaaf | 36.5 – 76.5 | 200 | Over |
| ncaab | 130 – 172.5 | 200 | Over |
| nba | 208.5 – 255.5 | 200 | Under |

The normal CDF then saturates, which is why `model_prob` is **exactly 1.0**
on every row that has one and `edge_pct` **exactly 50.0** on 113 of 147.

Graded, it came out at 52.0% over 50 picks at −110, ROI −0.007 — precisely
what picking a side by constant and paying the vig produces.

The eight rows with an edge above 100% are older still: all written
2026-03-15..03-18, before the current formula, which caps at 50 by
construction.

### The fix: no signal, no bet

`TeamStats.ratings_measured` records whether the three ratings came from real
`TeamStat` rows or from their fallbacks. `_build_game_data` sets it, and the
ensemble's totals branch is gated on both sides having it.

Explicit rather than sniffing for 100.0: a genuine 100.0 and a missing value
are different facts even when they are the same number.

**This stops over_under picks entirely in production today**, because nothing
supplies the ratings — verified against live upcoming games, all `False`.
Moneyline and spread are untouched; spread uses the LightGBM margin path, not
these features.

Over_under was the largest market in the table (361 picks before dedup). It
was also the one with no demonstrated skill, so the trade is giving up volume
that was costing the vig.

### To turn totals back on

Supply the ratings, or replace `_predicted_total` with something built from
data that exists. Scores are in `games.home_score`/`away_score`, so a
points-for / points-against average per team is reachable; it needs new
`TeamStat` rows from `_refresh_team_stats` and its own validation before
anything should bet on it. Not attempted here.

## The points-for totals model — 2026-09-19

Replaces the constant-200 predictor. Reproducible:

    python -m backend.analysis.totals_report --db <abs path>

### What was built

`points_for` and `points_against` join `COMPUTED_STAT_TYPES` in
`team_stats.py`, computed by the same `strictly_before` filter as everything
else, so a game's own score can never reach its own features.

They are **omitted, not defaulted, when a team has no history.** Unlike
`rolling_point_diff`, where 0.0 is a sensible neutral margin, zero points
scored would make the model predict a 0-0 game. `rolling_point_diff` is now
derived from the two means, so the three stats agree by construction.

`_predicted_total` is the mean of the two teams' typical game totals.

### It does predict. It does not beat the line.

| sport | n | MAE | resid sd | legacy(200) MAE |
|---|---|---|---|---|
| nba | 1229 | 15.61 | 19.57 | 31.62 |
| mlb | 79 | 3.22 | 4.21 | 191.99 |
| ncaab | 22 | 12.75 | 16.03 | 50.18 |

Against the market line, which is what a bet is actually against:

| sport | n | our MAE | line MAE | beats line |
|---|---|---|---|---|
| nba | 42 | 14.41 | 11.50 | **no** |
| ncaab | 16 | 11.99 | 6.99 | **no** |
| mlb | 11 | 3.45 | 3.92 | yes, but n=11 |

**So `TOTALS_VALIDATED_SPORTS` is empty and no totals picks are generated.**
Beating a constant by 2x is not an edge. Disagreeing with a line we are
measurably worse than just means we are wrong, which is exactly what the old
version did for months at 52.0% and −0.007 ROI. mlb is the only candidate and
11 games cannot carry that decision.

Add a sport to that frozenset when `totals_report` says it beats the line on
a real sample. That is the whole gate.

### The assumed spreads were too small

`TOTAL_POINTS_STD` is the standard deviation of *the model's own residuals*,
which is what the over/under CDF needs. Every measured value is larger than
the guess it replaced — nba 15.0 → 19.6, ncaab 12.0 → 16.0, mlb 4.0 → 4.2.
A too-small value makes the model overconfident and inflates every edge it
claims. nfl, ncaaf, boxing and mma remain unmeasured guesses.

### A docstring that claimed more than the arithmetic did

The first version described the standard matchup form — each side scoring
the mean of its own rate and the opponent's concession rate. Mutation
testing found no test could tell the two apart, because they are the same
number:

    (hf + ad)/2 + (af + hd)/2  ==  (hf + hd + af + ad)/2

The opponent adjustment cancels in the sum. It only changes how the total
splits between the sides, which a total discards. The code now says what it
does, and a test pins the equivalence so the claim cannot creep back.

## Home/away scoring splits: built, measured, left off — 2026-09-19

`points_for_home`, `points_against_home`, `points_for_away`,
`points_against_away` join `COMPUTED_STAT_TYPES`, and the totals model can
use the home side's home history against the away side's road history
instead of one blended rate.

Two rules they follow:

- **A neutral-site game belongs to neither venue.** It is real evidence of
  scoring but not of home-court scoring, and ncaab's entire stored history is
  neutral-site bracket games. Neutral games stay in the blended rates and are
  excluded from the splits — and a neutral game ignores splits at prediction
  time too.
- **A thin split is worse than none.** `MIN_VENUE_GAMES = 5`; below that the
  key is omitted and the blended rate is used.

### The measurement says it changes nothing

Predicted beforehand from the data: the real per-team venue effect on nba
game totals is about **1.68 points sd** (observed spread 4.99 against 4.70
expected from sampling noise alone, ratio 1.06, mean difference exactly
+0.000), while splitting raises each estimate's standard error from
21.4/√78 = 2.42 to 21.4/√39 = 3.42 — roughly 2.42 points of added estimation
noise to recover 1.68 points of signal.

Measured after building, on 1068 nba games carrying both splits:

```
paired |error| difference, split minus blended
  mean     -0.0278 points of MAE
  t        -0.20      95% CI  -0.297 .. +0.241
  => 0.18% of a ~15.6 MAE, indistinguishable from zero
```

`USE_VENUE_SPLITS = False`. The prediction was "slightly worse"; the
measurement says "neither". Both agree there is nothing here, so the simpler
predictor stays.

**This does not change the betting gate.** `TOTALS_VALIDATED_SPORTS` is still
empty — splits or not, the model does not beat the market line.

### Where splits might still earn their place

ncaaf's hosted games show a home/away scoring gap of **22.1 points** against
nba's 1.6, on 19 games. If that survives a real sample it is a far larger
venue effect than nba's, and ncaaf has no splits yet — 19 games cannot give
any team `MIN_VENUE_GAMES` at a venue. Worth re-running `totals_report` once
a season of ncaaf has accumulated.

## Opponent-strength adjustment: measured, and switched ON — 2026-09-19

`points_for_adj` / `points_against_adj` shift each prior game by how much
that opponent usually concedes or scores, relative to the league, over the
same point-in-time pool.

### Why this one was worth building and the venue splits were not

Over a full nba season, opponent defence faced varies by only **0.27 points
sd** across teams — a balanced schedule leaves nothing to correct. But the
model reads a **10-game window**, and there it varies by **1.45** (offence
faced, 0.97), because ten games of eighty-two are nowhere near balanced. A
total sums four such terms, so the correction has sd ≈ 2.89 points against a
model MAE of 15.6.

It also costs no sample: it re-weights the same games rather than halving
them, which is what sank the venue splits.

### Measured, paired on identical games

| sport | n | adj MAE | raw MAE | paired t | 95% CI |
|---|---|---|---|---|---|
| nba | 1229 | 15.50 | 15.61 | **−2.61** | −0.206 .. −0.029 |
| mlb | 79 | 2.95 | 3.22 | −1.12 | −0.739 .. +0.201 |
| ncaab | 22 | 11.45 | 12.75 | −0.58 | −5.690 .. +3.092 |

`USE_OPPONENT_ADJUSTMENT = True`. nba's interval clears zero (p ≈ 0.009) and
all three point the same way. The effect is small — 0.75% of MAE — which is
close to what the schedule-variation argument predicted. Compare the venue
splits at t = −0.20, which stay off.

`TOTAL_POINTS_STD` is re-measured for the model **as configured**, with the
adjustment on: nba 19.5, ncaab 13.4, mlb 3.7. Re-measure whenever a switch in
`ensemble.py` changes.

### It still does not beat the line

| sport | n | adj MAE | line MAE |
|---|---|---|---|
| nba | 42 | 14.24 | 11.50 |
| ncaab | 16 | 10.18 | 6.99 |
| mlb | 11 | 3.84 | 3.92 |

`TOTALS_VALIDATED_SPORTS` stays empty and no totals picks are generated. The
adjustment moved nba from 14.41 to 14.24 against a line at 11.50; a 0.17
gain does not close a 2.74 gap.

### A data-quality finding this turned up

Three **All-Star exhibition games** sit in `games` as nba finals — 2026-02-15,
with squad teams "Team Stars", "World" and "Team Stripes", and totals of 72,
82 and 93 against a real nba average of 230.9.

They are not in the totals validation set, but they **are** in
`CalibratedModel.train_from_db`, which selects every final game with no sport
filter. They also dragged my own league-average baseline down by 6.5 points
and produced an impossible all-positive schedule-strength deviation, which is
how they were noticed.

Three games of 1248 is a small contamination, and the squads are separate
`Team` rows so no real team's stats are touched. Not fixed here.

## Pace and rest: neither can be added — 2026-09-19

Both were measured. Neither produced a feature.

### Pace is not computable from the data this repo holds

Possessions are `FGA − ORB + TOV + 0.44 × FTA`. `player_stats` carries
minutes, points, rebounds, assists, threes, steals, blocks and turnovers —
**no field-goal attempts, no free-throw attempts, and rebounds are not split
offensive/defensive.** Three of the four inputs are missing.

This is the same wall the calibration report's standing caveat describes, and
it is why `offensive_rating`, `defensive_rating` and `pace` were never
written. Nothing has changed that.

For a totals model it matters less than it sounds: pace only reaches the
scoreboard through points, and `points_for + points_against` already embeds
pace × efficiency. A separate pace term would only help if pace were more
*stable* than the combined rate — which cannot be tested without possessions.
Manufacturing a "pace" feature out of points would be circular.

### Rest predicts nothing, and the signal that looks like rest is a phase effect

The pooled regression is tempting: **slope −0.342 points of total per extra
day of combined rest, t = −2.25.** It does not survive splitting by phase.

| phase | n | mean combined rest | mean total | mean residual |
|---|---|---|---|---|
| regular season | 1208 | 4.27 | 231.1 | −0.17 |
| postseason | 21 | **19.62** | **210.5** | **−17.09** |

| slope of residual on rest | n | slope | t |
|---|---|---|---|
| pooled | 1229 | −0.342 | −2.25 |
| within regular season | 1208 | +0.135 | +0.47 |
| within postseason | 21 | −0.123 | −0.49 |

Rest predicts nothing inside either group. The pooled effect is entirely
between-phase: playoff games carry a long layoff **and** score about 20 points
less. A rest adjustment fitted on the pooled data would be a playoff detector
wearing a rest costume, and it would misprice every well-rested
regular-season game.

Nothing was added to the model. `totals_report` now prints residual bias
split at `LONG_LAYOFF_DAYS = 10` so the question stays answerable:

```
  sport    normal n     bias  layoff n     bias
  nba          1196    -0.50        33    -4.37
  mlb            64    +0.38        15    +0.94
```

### The finding worth more than either of them

**The model over-predicts postseason totals badly.** Playoff games in the
table average 210.5 against a regular-season 231.1, and the model — fitted on
regular-season scoring rates — carries a **−17.09 mean residual** on them.

That is a far larger effect than opponent strength (which moved MAE by 0.11)
and it is a bias, not noise. n=21 is too thin to calibrate a correction, and
the postseason data is sparse for other reasons, but a phase indicator is the
obvious next lever if playoff coverage improves.

## The postseason bias, fixed — 2026-09-19

`games.season_type` now records ESPN's season phase, and the totals model
corrects a measured per-sport bias on it.

### Making the phase knowable

ESPN reports it at **event** level: `event["season"]["type"]` — 1 preseason,
2 regular, 3 postseason, 4 all-star. Read the event's block, not the
league's: on a playoff date the league block still says type 2, so the
obvious field is the wrong one.

`backfill_neutral_site` is now `backfill_game_flags` and fills both
ESPN-sourced flags in one pass, since it already matches by `espn_id`:

    python -m backend.scripts.backfill_game_flags --db <abs path>

Backfilled: nba 1231 regular / 21 postseason, ncaab 9 regular / 65
postseason, mlb 112 regular, ncaaf 75, nfl 1. No unmatched rows.

**A long layoff is not a usable proxy for this**, which is why the field was
needed: of 33 long-layoff nba games only 10 are postseason, and the other 23
are in-season breaks (December, and the February All-Star break) whose bias
is **+2.08** — the opposite sign.

### The correction is per sport, not per phase

| sport | phase | n | bias | t |
|---|---|---|---|---|
| nba | regular | 1208 | −0.17 | −0.30 |
| **nba** | **postseason** | **21** | **−17.09** | **−3.80** |
| ncaab | postseason | 20 | −1.58 | −0.50 |
| mlb | regular | 79 | +0.54 | +1.30 |

**ncaab postseason shows no bias at all.** A global "playoff games score
less" rule would have been wrong for it. The NCAA tournament is
single-elimination at roughly regular-season pace; an nba playoff series is
seven games of tighter defence and shorter rotations. `TOTAL_BIAS_BY_PHASE`
is therefore keyed on `(sport, phase)` and holds only measured entries.

### Result

| sport | phase | n | bias before | after | MAE before | after |
|---|---|---|---|---|---|---|
| nba | postseason | 21 | −17.09 | **+0.01** | 20.73 | **17.05** |

Everything else is untouched by construction. Overall MAE across all 1330
scored games moves 14.685 → 14.627.

**This is one postseason of evidence.** The interval is roughly −26.5 to
−7.7: the direction is not in doubt, the magnitude is. Re-measure with
`totals_report` as playoffs accumulate.

### A correction to an earlier note

I suggested `season_type` would also separate the three All-Star exhibition
games. **It does not** — ESPN labels them type 2, regular season, so they are
stored as `regular`. They remain in `CalibratedModel`'s training pool with
totals of 72, 82 and 93. Identifying them needs something else, probably
`competition.type.abbreviation`, which reads "STD" for a normal game and
"SEMI" for a conference final.

## All-Star games separated — 2026-09-19

### The season block could not find them

ESPN labels All-Star games `season.type = 2` — regular season — so
`season_type` derived from that block alone stored them as `regular`. The
**competition** block does distinguish them:

| game | `season.type` | `competition.type.abbreviation` |
|---|---|---|
| normal | 2 | `STD` |
| conference final | 3 | `SEMI` |
| **All-Star** | **2** | **`ALLSTAR`** |

`espn.season_type_of(event, competition)` now prefers the competition block
where the two disagree, because it is the more specific one. Backfilled: the
three rows are now `allstar`, and nothing else in nba changed (1249 already
correct).

The separation is clean — nba `allstar` averages a total of **82.3** against
`regular` at 231.2.

### They are out of the model's training pool

`CalibratedModel.train_from_db` selected every final game with no filter. It
now excludes `NON_COMPETITIVE_PHASES = ("allstar", "preseason")`: 1435 final
games, 3 excluded, 1432 trained on.

`unknown` is deliberately kept — it is what every row predating the column
carries, and dropping those would discard most of the history. `postseason`
is kept too: playoff games are real results, however differently they score,
and the totals model corrects their bias rather than ignoring them.

### What this did and did not affect

The All-Star squads are separate `Team` rows and **only ever play each
other** — 0 games pair a squad with a real team — so no real team's scoring
history, Elo or record was ever polluted. The contamination was confined to
the training pool and to any league-wide average computed over `games`,
which is how it was found: it dragged the nba baseline down 6.5 points and
produced an impossible all-positive schedule-strength deviation.

ESPN lists a **fourth** All-Star event that day, `401838143` (the
Championship), which is not in the table at all. Not chased.

## The missing All-Star event, and what it led to — 2026-09-19

`401838143` (the All-Star Championship) was absent. Chasing it found a
matcher bug and, separately, a coverage gap.

### The bug: a same-day rematch was folded into the first game

`fetch_and_store_games` looks a game up by `espn_id`, then falls back to
`(sport, date, home_team, away_team)` — a fallback whose comment says it is
"for rows created before espn_id existed", but which had no `espn_id`
constraint. So it also matched rows that already carried a **different**
espn_id, and a genuinely distinct second meeting was folded into the first
and lost.

Three real games were lost this way:

| event | what it was |
|---|---|
| `401815461` | mlb CIN/STL 2026-05-23, the second of a doubleheader (17:10Z and 23:15Z share an Eastern date) |
| `401873649` | mlb BAL/DET 2026-05-24, same |
| `401838143` | nba All-Star Championship, repeating the round robin's STRIPES v STARS pairing |

Fixed by adding `Game.espn_id.is_(None)` to the fallback, which is what the
comment always claimed it did. All three have been recovered: games 1832 →
1835, `integrity_check: ok`.

The fallback's real purpose still works — rows created from the Odds API
carry no ESPN identity and are still adopted rather than duplicated.

### The coverage gap: four nba games were never collected

`401810284` (NO/PHX 2025-12-26), `401810547`, `401810600`, `401810627`. These
are **not** the matcher bug: each has an Eastern date of its own and no
same-date rematch. The stored row for that pairing is the *next day's*
different event, carries `start_time = None`, and was created from the Odds
API and later given its id by `backfill_espn_ids` — so the ESPN games path
never saw that date at all.

They date from the months the scheduler was producing nothing. Four of 1245
nba games. Not a code defect and not repaired.

### A measurement error worth recording

The first sweep compared ESPN's events against our rows **per date** and
reported 741 missing across 125 dates. That was wrong: ESPN files by Eastern
date and a game stored under a neighbouring date counted as absent. Checking
each espn_id against the whole table gives **7**. Compare identities against
the whole set, not per bucket, whenever the bucket key is itself uncertain.

## The four missing games — and eight wrong scores — 2026-09-19

All four were fetched: `401810284` (NO/PHX 2025-12-26), `401810547`,
`401810600`, `401810627`. Games 1835 → 1839.

### Fetching them exposed something worse

The newly fetched NO/PHX game came back 108-115 — **the same score already
stored on the 12-27 game**. ESPN says 12-26 was 108-115 and 12-27 was
114-123, so the stored row was wearing the wrong game's result.

An audit of every stored final against ESPN — **1393 checked, 8 mismatched**:

| game | stored | actual |
|---|---|---|
| nba 469 (2025-12-27) | 108-115 | 114-123 |
| nba 745 (2026-02-01) | 118-125 | 134-91 |
| nba 790 (2026-02-07) | 135-115 | 122-115 |
| nba 817 (2026-02-11) | 102-95 | 102-105 |
| nba 844 (2026-02-20) | 112-105 | 131-118 |
| mlb 1600/1601/1602 (2026-05-24) | — | — |

Four of the five nba rows are exactly the pairings whose twin was missing:
they had absorbed the adjacent fixture's result.

### A wrong score could never heal

`fetch_and_store_games` wrote scores **only on the transition to final**:

```python
if g["status"] == "final" and existing.status != "final":
```

so a row that was already final kept whatever it had, permanently, no matter
how many times the date was re-fetched. It now corrects a final game whose
score disagrees with ESPN, and logs each correction. A payload with no score
still never overwrites a stored one — ESPN reporting nothing is not ESPN
reporting 0-0.

All eight were repaired by re-running their dates through the fixed code.
**Re-audit: 1390 checked, 0 mismatched.**

### Blast radius

**0 picks and 0 graded results** on any of the eight, so no ROI, grading or
pick history was affected. Only `elo_history` (16 rows) and `team_stats` (232
rows) depended on them, and both were rebuilt — nba and mlb recomputed with
`--force`, 1253 and 96 games. `integrity_check: ok`.

Inserting historical games invalidates every point-in-time stat computed
after them, so that rebuild is not optional; it is part of the repair.

### Notes

- A later absence sweep reported "77 missing". That set excluded
  scheduled and postponed rows, of which 79 carry espn_ids. Not a real gap.
- That sweep also hit `httpx.ReadTimeout` after ~200 rapid requests.
  `espn_http.get_with_retry` retries on status codes only, not on timeouts.
  Worth widening.

## The ESPN retry now covers transport failures — 2026-09-19

`get_with_retry` looked only at status codes, so a read timeout propagated on
its first occurrence. That is inconsistent on its face: the same throttling
that returns a 403 also stalls a read, and treating one as retryable and the
other as fatal cost a whole audit sweep to `httpx.ReadTimeout` after about
200 rapid requests.

It now retries `httpx.TransportError` — timeouts, refused connections, reads
that die mid-body. Deliberately **not** `Exception`: anything else is a bug
in this code and retrying it only delays the traceback four-fold.

Three properties the tests pin, each confirmed by mutation:

- **A persistent transport failure is raised, not swallowed.** There is no
  response to return, and returning `None` would break every caller's
  `raise_for_status`.
- **Status and transport retries share one attempt budget.** A request
  alternating between a timeout and a 403 would otherwise never stop.
- **A non-transport exception is not retried at all.**

Verified by re-running the sweep that died: 195 dates, **completed in 30s**,
0 events absent — which also confirms every recovered game is now present.

**The fix was not exercised against a real timeout on that run**: no retry
warnings appeared, so the network was simply healthy. It is proven by the
tests, not by reproducing the production failure.

## Boxing odds were never fetched at all — 2026-09-19

Not "games without odds". **Nothing fetched boxing or mma odds since
2026-05-24**, and the rows that looked like unpriced fixtures were the
residue of that last run.

### The structural gap

```
fetch_and_store_odds  <- called ONLY from _run_window
_run_window jobs      <- created only for scheduled_sports
scheduled_sports      <- active_sports INTERSECT ESPN_TEAM_SPORTS
ESPN_TEAM_SPORTS      <- ("nba","nfl","ncaab","ncaaf","mlb")
```

A window is clustered around ESPN start times, so it only exists for sports
with an ESPN schedule. boxing and mma have none — *their games are created by
the odds fetch itself* — so they fell out of the only code path that fetches
odds. They are in `ALL_SPORTS` and in season all year, and were simply never
reached.

`windowless_sports()` and `fetch_windowless_odds()` now run from
`morning_scout` for exactly those sports. Failures are logged, not raised:
boxing being unavailable must not cost the rest of the scout.

### Measured

The Odds API had **39 boxing events**, 28 of them real upcoming fights. The
table held none of those. After one fetch:

| sport | upcoming games | with odds |
|---|---|---|
| boxing | 28 real fights | **28** |
| mma | 41 | 35 |

109 boxing and 92 mma odds rows stored, 2 credits.

### Still open: the 2026-12-31 placeholder rows

25 boxing and 10 mma games sit on `2026-12-31`, which is the Odds API's date
for an undated event. They are not fixtures — they are speculative matchup
markets: eleven different "Moses Itauma vs X", seven "Daniel Dubois vs X".

**They will be picked on 2026-12-31**, because `generate_and_store_picks`
filters on `Game.date == target_date` and nothing marks them as
hypothetical. That is a latent problem, not a current one, and it is not
fixed here. The cleanest signal is probably that a real fight's
`commence_time` carries a time of day while these do not.

## Speculative matchups no longer become fixtures — 2026-09-19

The Odds API sells futures — "who will X fight next" — as ordinary events,
**structurally identical to a real bout**. Same keys, same shape; the raw
payload for a Joshua-Fury future and a real Kameda-Hernandez bout differ only
in their values. They all carry the far-future `commence_time` the API uses
for an undated event, and `_ensure_game_from_odds` made a Game row for each.

### The detector is an invariant, not a date rule

**A competitor cannot face two different opponents at the same time.**
`speculative_competitors()` groups an event list by date, and any name with
more than one distinct opponent that day has all of its bouts skipped —
there is no way to tell which, if any, is real.

This passes a doubleheader (the same pair twice, which mlb does routinely)
and catches a futures block (one name against many). A Dec-31 rule would
have done neither: it would have broken on the mma futures dated
**2027-01-01, 2027-04-25 and 2027-08-01**, which this catches.

Against the live API: boxing 39 events → keep 28, skip 11. mma 31 → keep 22,
skip 9. Every kept boxing event is on a real date.

Scoped to the odds path. ESPN-sourced games genuinely can have one side
facing several opponents in a day — an All-Star round robin does.

### Retroactive cleanup had to be narrower, and here is why

Applying the same invariant to the stored table flags **50** rows, but 12 of
them are on real past dates and must not be deleted. The table accumulates
across API snapshots, so an opponent change leaves both rows behind: boxing
2026-03-21 has "Leli Buttigieg vs Jake Goodwin" *and* "Emmanuel Buttigieg vs
Jake Goodwin", and one of those is a real fight.

Prevention sees a single snapshot where everything is live at once; cleanup
sees history. Only the 38 rows on a placeholder date were deleted — 0 picks
and 0 graded results depended on them, 14 odds rows went with them,
`integrity_check: ok`, `foreign_key_check` clean.

| | before | after |
|---|---|---|
| boxing upcoming with odds | 29 / 53 | **28 / 29** |
| mma upcoming with odds | 35 / 41 | **24 / 27** |

### Known limitation: a one-off future survives

The invariant needs a repeated name. Seven combat rows remain on a
placeholder date because no competitor repeats *on that date*:

```
mma     2026-12-31  Islam Makhachev vs Kamaru Usman
mma     2026-12-31  Tom Aspinall vs Ciryl Gane
boxing  2026-12-31  Dalton Smith vs Adam Azim
mma     2026-12-31  Max Holloway vs Paddy Pimblett
mma     2027-07-01  Islam Makhachev vs Kamaru Usman
mma     2027-07-02  Islam Makhachev vs Kamaru Usman
mma     2027-07-10  Paddy Pimblett vs Conor McGregor
```

Bookmaker count is not a usable second signal — these carry 0-2 books, but so
does a real small-hall fight (Nelson Birchall vs Lewis Morris, 1 book, a
genuine 2026-09-26 bout). A horizon cap would catch the 2027 rows but not
2026-12-31, which is only three months out.
