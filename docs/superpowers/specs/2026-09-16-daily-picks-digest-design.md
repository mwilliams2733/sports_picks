# Daily Picks Digest — Design

**Date:** 2026-09-16
**Status:** Approved for planning
**Base commit:** `0e1f13f`

## Context

`sports_picks` generates and grades betting picks daily via an APScheduler cron
set (`morning_scout` at 8/9/10am ET, recalibration at 3am). Picks are persisted
to the `picks` table; player props to `player_props`. Everything is currently
consumed through the React SPA — there is **no outbound notification of any
kind**, and no email infrastructure in the repository.

The owner wants a daily email to himself and a few friends listing the day's
best-rated picks per sport, each with a short plain-language rationale that
gives the reader "something to research."

## Goals

1. One email per day at **11:00 ET**, after the morning scout has run.
2. **Top 5 game picks per active sport**, ordered within each sport.
3. A **separate props section**, top 5 props per active sport.
4. A **brief, plain-language rationale per pick** — factors, not mathematics.
5. Professional-looking HTML that renders correctly in common mail clients.
6. Sent to a small, fixed list of recipients.

## Non-goals

- **Golf, tennis and soccer.** Requested, but none of the three exists anywhere
  in the system — no collectors, ratings, strategies, season config, or
  `SPORT_KEYS` entries. Each is a separate multi-day pipeline project, and they
  are not equivalent in difficulty:
  - *Tennis* is 1v1 and maps onto the existing combat-sports Elo path.
  - *Golf* is a field event (outrights, top-N, matchups); `Game(home_team_id,
    away_team_id)` cannot represent it.
  - *Soccer has draws.* `away_prob = 1.0 - home_prob` is assumed in every
    strategy variant and `grade_pick` has no three-way moneyline concept.
    Supporting it means changing the probability model system-wide.

  These get their own specs. This design must not assume two-outcome markets in
  the digest layer, but it also does not attempt to solve for three.
- Unsubscribe flows, bounce handling, per-recipient preferences, a recipients
  table. The list is small, known, and lives in config.
- Any change to how picks are *generated*. The digest reads what the pipeline
  already produced.
- Web or push delivery.

## Architecture

Six units, each independently testable:

```
strategies ──emit──▶ Pick.factors ──persist──▶ picks.rationale_json
                                                      │
                                                      ▼
                                          digest/selector.py   (pure)
                                                      │
                                                      ▼
                                          analysis/rationale.py (pure)
                                                      │
                                                      ▼
                                           digest/render.py     (pure)
                                                      │
                                                      ▼
                                           digest/sender.py     (I/O)
                                                      ▲
                                        scheduler cron 11:00 ET
```

Three of the four new modules are pure functions with no I/O, so the whole
selection-and-rendering path is testable without a network or a clock.

### 1. Rationale capture

**Problem.** Strategies emit only a final number. `Pick` carries
`model_probability`, `implied_probability` and `suggested_unit_size`, and
`pick_generator` persists **none** of them — `picks.model_prob` was migrated in
(`database.py:migrate_pick_model_prob`) and has never been written to.

**Decision.** Strategies emit *structured factor codes*, not prose and not
numbers-only. Considered and rejected:

- *Recompute factors at digest time from `GameData`* — no schema change, but it
  derives the reasoning separately from the pick that was actually made. The two
  can drift, and the email would then state a reason the model did not use.
  Violates the repo's "derive, don't duplicate" rule in the way that matters
  most: the duplicate would be presented to readers as fact.
- *Free-text reason per strategy* — prose scattered across five files,
  inconsistent voice, untestable.

**Shape.** A new frozen dataclass in `backend/data_types.py`:

```python
@dataclass(frozen=True)
class PickFactor:
    code: str      # e.g. "rating_gap", "schedule_fatigue"
    side: str      # "home" | "away" | "over" | "under"
    strength: str  # "slight" | "moderate" | "strong"
```

`Pick` gains `factors: list[PickFactor] = field(default_factory=list)`.

**Factor vocabulary** — every code must correspond to a signal the strategy
actually computed. Initial set, grounded in existing code:

| Code | Source |
|---|---|
| `rating_gap` | Elo / rating differential (`elo.py`, `EloRating`) |
| `recent_form` | recent point differential (`pd_score`) |
| `home_advantage` | `get_home_win_rate(sport)` |
| `schedule_fatigue` | `_check_schedule_fatigue` — 3 games in 4 nights (NBA/NCAAB) |
| `lookahead_spot` | `is_lookahead_spot` |
| `pitcher_edge` | `pitcher_skill_score` (MLB) |
| `model_consensus` | `_count_agreeing_models` / `_count_agreeing_signals` |
| `line_value` | model probability vs de-vigged market price |

Prop factors, from `prop_analyzer`'s existing inputs:

| Code | Source |
|---|---|
| `season_avg_vs_line` | season average vs the posted line |
| `recent_trend_vs_line` | recent game logs vs the posted line |
| `low_sample` | fewer than 3 game logs — a **caveat**, renders as a warning |
| `high_variance` | wide residual spread relative to the line |

**Persistence.** New column `picks.rationale_json TEXT NULL`, added by a
`migrate_pick_rationale(engine)` following the existing pattern in
`database.py` and wired into `create_app`. `pick_generator` writes both
`rationale_json` and the long-dead `model_prob` at the same time.

**Rendering.** `backend/analysis/rationale.py` owns every human-readable string:

```python
def render_factor(factor: PickFactor, game: Game) -> str: ...
def render_rationale(factors: list[PickFactor], game: Game, limit: int = 2) -> str: ...
```

Output is a sentence or two naming at most the two strongest factors, using team
names:

> Rating gap strongly favors Kansas City; Buffalo on its third game in four nights.

Unknown codes render as nothing rather than raising — a future strategy adding a
factor must never break the digest.

### 2. Digest selection

`backend/digest/selector.py`, pure, no I/O. Takes already-loaded rows, returns a
structure.

**Active sport** = in season per `is_sport_in_season(sport, seasons, target_date)`
**and** has at least one `Game` on `target_date`. A sport with no games does not
appear at all.

**Target date** is the current **ET** calendar date at send time, matching the
scheduler's `ET` timezone. `Game.date` is a plain `Date`; no conversion needed.

**Ranking**, applied per sport:
1. `confidence` descending (5 → 1)
2. `edge_pct` descending
3. `game.start_time` ascending, then `pick.id` ascending — deterministic ties

Take at most 5. **Never pad**: if two picks clear the threshold, two are shown.
Padding would print picks the model does not endorse, which defeats a "top rated"
list. Picks with `confidence < 1` are excluded entirely.

**Props are ranked separately**, top 5 per active sport, and are never merged
into the game-pick ranking. Prop `edge_pct` is computed as
`(directional_prob - 0.5) * 200` and **ignores the prop's price** (audit finding
#9), so it is not on the same scale as a game pick's de-vigged edge. Ranking them
together would systematically float juiced props above better game picks. When
that finding is fixed, merging becomes possible; this design does not depend on
it.

**Sports included** are configurable, defaulting to what was asked for and
exists: `["ncaaf", "nfl", "nba", "mlb"]`. `boxing`, `mma` and `ncaab` are
supported by the pipeline and can be added to the list without code changes.

### 3. Rendering

`backend/digest/render.py`, pure: selection result in, `(subject, html, text)`
out.

- Table-based HTML with inline styles — the only reliable approach across Gmail,
  Outlook and Apple Mail. No external CSS, no web fonts, no JavaScript.
- Mobile-first single column; readable at 360px.
- A plain-text alternative is generated alongside, always.
- Per pick: sport, matchup, pick value, American odds, confidence stars, edge %,
  and the rendered rationale.
- Subject line names the date and pick count, e.g.
  `Top picks — Sat Sep 20 (18 across 4 sports)`.
- A short header states what the list is and a one-line footer notes these are
  model outputs for research, not advice.
- **Empty digest**: if no sport is active or nothing clears the threshold, no
  email is sent at all. A daily "nothing today" email trains recipients to
  ignore the sender.

### 4. Delivery

`backend/digest/sender.py`, the only unit that touches the network.

- Provider: **Resend**. Free tier covers this volume, gives a real From address
  and materially better inbox placement than Gmail SMTP.
- `RESEND_API_KEY` is read from the environment, populated from
  `C:\Users\mwill\.secrets\shared.env`. Referenced by name only; never logged,
  never echoed, never committed. `.env.example` gains the key name with a
  placeholder value.
- Recipients and settings live in `config.yaml`:

```yaml
digest:
  enabled: false          # must be explicitly turned on
  send_hour_et: 11
  sports: ["ncaaf", "nfl", "nba", "mlb"]
  max_per_sport: 5
  from: "picks@example.com"
  recipients:
    - "you@example.com"
```

- **Dry-run mode** (`enabled: false`, or `DIGEST_DRY_RUN=1`) writes the rendered
  HTML to a local file and sends nothing. This is the default.
- Any error from the provider is logged with the API key scrubbed — reuse
  `redact_api_key`'s pattern; a provider error body can echo the request.

### 5. Scheduling

One job, matching the three existing ones exactly
(`backend/pipeline/scheduler.py:configure_scheduler`, which already runs on `ET`):

```python
scheduler.add_job(
    lambda: send_daily_digest(config, engine),
    'cron', hour=config.get("digest", {}).get("send_hour_et", 11), minute=0,
    id='daily_digest', replace_existing=True,
)
```

Registered only when `digest.enabled` is true or dry-run is set, so test runs
that construct the scheduler do not acquire a mail job.

### 6. Error handling

- `send_daily_digest` catches every exception, logs it, and returns. It must
  never raise into APScheduler — a failed digest must not take down the job that
  also owns pick generation's sibling schedule.
- Selection and rendering failures are logged with the target date.
- No retry. A missed digest is visible to the reader; a duplicate or a garbled
  one is worse.
- Send is **not** idempotent-guarded in v1 beyond the single cron trigger. If
  the process restarts at 11:00 twice, two emails send. Accepted: the scheduler
  runs one instance, and the cost of a duplicate is low.

## Data model changes

| Change | Where |
|---|---|
| `picks.rationale_json TEXT NULL` | new `migrate_pick_rationale` in `database.py`, called from `create_app` |
| `picks.model_prob` now actually written | `pick_generator` — column already exists, never populated |

No other schema changes. No Alembic introduction — this follows the existing
hand-rolled migration pattern rather than mixing two approaches mid-project.

## Testing strategy

- `rationale.py`: unit tests per factor code, both sides, all three strengths,
  plus the unknown-code case rendering empty rather than raising.
- `selector.py`: active-sport detection (in season + has games), ranking order,
  the never-pad rule, deterministic tie-breaking, props ranked separately,
  empty-day returns empty.
- `render.py`: snapshot-style assertions on HTML and text — no network, no clock.
  Assert specific content (a team name, an odds string), not just "html is
  non-empty".
- `sender.py`: dry-run writes a file and performs no network call; a provider
  error is caught and the API key never appears in the captured log.
- `scheduler`: the digest job is registered when enabled and **absent** when
  disabled.
- Every new guard must be shown to fail against unfixed code before being
  treated as a guard, per repo convention.

## Sequencing and dependencies

**This design must not be pointed at real recipients until plans 002 and 003 are
merged.** As of `0e1f13f`:

- 002 (reconnect the calibrated model, thresholds, `receptions`) is complete and
  awaiting review.
- 003 (remove vig in the four strategies that don't; fix probability-space odds
  averaging) is **not started**. Until it lands, `edge_pct` on four of five
  strategies includes the bookmaker's overround, so the "top rated" ordering is
  sorting by a number that is systematically ~2pp too high.

Build and dry-run now; enable real recipients after 003. The `enabled: false`
default enforces this by construction.

Implementation order:

1. **Rationale capture** — `PickFactor`, migration, persistence, `rationale.py`.
   The bulk of the work; touches all five strategy variants.
2. **Selector** — pure, depends on (1) only for the field being present.
3. **Renderer** — pure, depends on (1) and (2).
4. **Sender + scheduling + config** — smallest unit, depends on (3).

(1) is independently valuable: persisting `model_prob` and a rationale improves
the SPA and any future reliability analysis, whether or not the email ships.

## Open questions

None blocking. Two deliberate deferrals, recorded so they are not rediscovered:

- Merging props into the main ranking becomes reasonable once prop edge is made
  price-aware (audit finding #9). Not required here.
- A second afternoon send for evening slates was considered and declined for v1;
  the 11:00 ET send misses picks generated in later windows for late games.
