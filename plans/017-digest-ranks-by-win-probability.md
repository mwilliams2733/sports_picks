# Plan 017: The digest ranks picks by win probability and refuses longshots

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 84dc78c..HEAD -- backend/digest/selector.py backend/digest/job.py config.yaml backend/tests/test_digest_selector.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `84dc78c`, 2026-09-23
- **Executor model**: `haiku` (Agent tool `model` value). Every edit is transcribed from literal code in Steps 1-4, the seven tests are fully specified (names, fixture rows, expected lists), and the mutation checks name the exact edit and the exact test that must fail. Nothing is written from prose. If any STOP condition fires or a mutation check does not behave as stated, re-dispatch on `sonnet` rather than let the cheap model improvise.

## Why this matters

The daily email picks its "top 5 per sport" by confidence stars and then by
`edge_pct`. Confidence is itself a threshold on edge, so the email is ranked
by edge. Edge is `model probability - market probability` in absolute
points, and the model is most overconfident on long-priced underdogs, so the
largest edges are the model's largest errors. The 2026-09-20 email led with
a +248 underdog at five stars.

Measured on the graded book (moneyline, five team sports, 129 decided picks,
pooled because each sport alone is small):

| price band | n | win % | units |
|---|---|---|---|
| favorite at -150 or shorter | 15 | 73% | +1.3 |
| favorite -149 to -101 | 14 | 43% | -3.4 |
| dog +100 to +200 | 42 | 40% | -3.0 |
| dog longer than +200 | 58 | 16% | -16.6 |

The 528-pick NFL backtest recorded at `backend/analysis/variants/ensemble.py:44-62`
says the same: dogs are 78% of the loss, and a price ceiling moves win rate
from 43.8% to 58.6%. After this plan, the email ranks by the model's own win
probability and never shows a price longer than a configured ceiling.

**Honesty note for the reviewer:** this raises the win rate of what is
emailed. It does not make the model profitable; the same backtest shows ROI
still slightly negative with the ceiling on. That is a model problem (plans
018 and 020 address parts of it), not a selection problem.

## Current state

- `backend/digest/selector.py` — chooses and ranks digest content. Pure:
  takes a session and a date, no network, no sending.
- `backend/digest/job.py` — orchestrates select → render → send once a day;
  reads the `digest:` block of `config.yaml`.
- `config.yaml` — `digest:` block holds `enabled`, `send_hour_et`, `sports`,
  `max_per_sport`, `from`, `recipients`.
- `backend/tests/test_digest_selector.py` — existing selector tests; the
  structural pattern to copy.

The sort key today, `backend/digest/selector.py:117-124`:

```python
        def _pick_sort_key(p):
            # start_time is nullable, and stored rows may be naive or aware.
            # ...
            st = games_by_id[p.game_id].start_time
            when = st.replace(tzinfo=None) if st is not None else datetime.max
            return (-p.confidence, -(p.edge_pct or 0.0), when, p.id or 0)
```

The game-pick query, `backend/digest/selector.py:107-113`, filters only on
game ids, `confidence >= 1` and `pick_type != "prop"`. There is no price
filter anywhere in the digest.

The `DigestPick` dataclass at `selector.py:16-24` carries
`odds: int` (from `p.odds_at_pick or -110`) and `edge_pct`, but not the
model probability. `PickModel.model_prob` exists (`backend/models.py:226`,
nullable Float) and is written by `generate_and_store_picks`.

`select_digest`'s signature, `selector.py:92`:

```python
def select_digest(session, target_date, sports, seasons, max_per_sport: int = 5):
```

and its caller, `backend/digest/job.py:29-35`:

```python
        sections = select_digest(
            session,
            target_date,
            cfg.get("sports", ["ncaaf", "nfl", "nba", "mlb"]),
            config.get("seasons", {}),
            max_per_sport=cfg.get("max_per_sport", 5),
        )
```

**The strategy already has a price-ceiling mechanism and it is off by
decision.** `ensemble.py:66` sets `DEFAULT_MAX_ODDS = None` with a comment
explaining the 2026-09-21 decision to keep generating underdog picks. Do not
flip that constant. This plan filters at the digest, so the picks still
exist for grading and measurement; only what gets emailed changes.

Conventions to match: the selector's docstrings explain *why* a rule exists
with the production incident that motivated it (see `_dedupe_latest`,
`selector.py:61-83`). Write the new docstrings that way. Tests build an
in-memory database with `_session()` and `_mk()` helpers; copy those.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Selector tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q` | all pass |
| Digest suite | `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py backend/tests/test_check_digest.py -q` | all pass |
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | all pass (~700, several minutes) |
| Dry-run the real digest | `DIGEST_DRY_RUN=1 .venv/Scripts/python.exe -c "import yaml;from backend.database import get_engine;from backend.digest.job import send_daily_digest;print(send_daily_digest(yaml.safe_load(open('config.yaml')), get_engine('sports_picks.db')))"` | prints a dict, writes `digest_preview.html`, sends nothing |

Run these from the repo root with the Bash tool (git-bash). There is no
ruff or mypy configured in this repo.

## Scope

**In scope** (the only files you should modify):
- `backend/digest/selector.py`
- `backend/digest/job.py`
- `config.yaml` (add one key under `digest:`)
- `backend/tests/test_digest_selector.py`

**Out of scope** (do NOT touch, even though they look related):
- `backend/analysis/variants/ensemble.py` — `DEFAULT_MAX_ODDS` is off by a
  recorded decision; the picks must keep being generated so they can be
  graded.
- `backend/digest/render.py` — the email body changes in plan 020.
- `backend/pipeline/pick_generator.py` — pick storage is not the problem.
- The props branch of `select_digest` — props have their own edge scale and
  their own ranking; leave the prop query alone.

## Git workflow

- Branch: `advisor/017-digest-ranks-by-win-probability`
- Conventional commits, as in `git log`: e.g.
  `fix(digest): rank by win probability, refuse longshots`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add the price ceiling to the selector

In `backend/digest/selector.py`, change the signature to:

```python
def select_digest(session, target_date, sports, seasons, max_per_sport: int = 5,
                  max_odds: int | None = 150):
```

After `picks = _dedupe_latest(picks)` for the **game picks only** (not the
props), add:

```python
        if max_odds is not None:
            # A pick with no stored price is kept: unknown is not a longshot,
            # and the renderer shows it at -110 anyway.
            picks = [p for p in picks
                     if p.odds_at_pick is None or p.odds_at_pick <= max_odds]
```

Put a docstring paragraph on `select_digest` explaining the filter with the
price-band table from "Why this matters", and stating that the ceiling is
applied here rather than in the strategy so that the picks still exist for
grading.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q` → all existing tests still pass (every fixture pick is at -110).

### Step 2: Rank by model win probability

Replace the game-pick sort key with:

```python
        def _pick_sort_key(p):
            st = games_by_id[p.game_id].start_time
            when = st.replace(tzinfo=None) if st is not None else datetime.max
            # Win probability first. Confidence is a threshold on edge, and
            # edge is model minus market in absolute points, which is largest
            # exactly where the model is most wrong (long-priced underdogs).
            # A pick with no stored probability sorts after every pick that
            # has one; among those, the old order still applies.
            prob = p.model_prob if p.model_prob is not None else -1.0
            return (-prob, -p.confidence, -(p.edge_pct or 0.0), when, p.id or 0)
```

The props keep calling the same function through their existing
`props.sort(key=_pick_sort_key)` line. That is intended: prop rows carry
`model_prob` too, and one key beats two.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q` → `test_ranks_by_confidence_then_edge` still passes, because its fixture rows have `model_prob=None` and fall through to the old order.

### Step 3: Thread the config through

In `backend/digest/job.py`, pass the new argument:

```python
            max_per_sport=cfg.get("max_per_sport", 5),
            max_odds=cfg.get("max_odds", 150),
```

In `config.yaml`, under `digest:`, add after `max_per_sport: 5`:

```yaml
  # Longest American price the email will show. Measured on the graded
  # book: picks longer than +200 win 16% of the time; favorites at -150 or
  # shorter win 73%. Picks past the ceiling are still generated and graded;
  # they are just not emailed. `null` disables the ceiling.
  max_odds: 150
```

**Verify**: `.venv/Scripts/python.exe -c "import yaml;print(yaml.safe_load(open('config.yaml'))['digest']['max_odds'])"` → `150`

### Step 4: Tests

Add to `backend/tests/test_digest_selector.py`. Do not change `_mk`; add a
second helper so existing tests are untouched:

```python
def _mk_priced(session, sport, gid, tid_h, tid_a, d, picks, pick_type="moneyline"):
    """picks: list of (confidence, edge, odds, model_prob)."""
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d,
                     home_team_id=tid_h, away_team_id=tid_a, status="scheduled"))
    session.flush()
    for i, (conf, edge, odds, prob) in enumerate(picks):
        session.add(PickModel(game_id=gid, strategy_id=1, pick_type=pick_type,
                              pick_value=f"P{gid}-{i}", confidence=conf,
                              edge_pct=edge, odds_at_pick=odds, model_prob=prob))
    session.commit()
```

Tests to write (one function each, names as given, all on `sport="nfl"`,
`date(2026, 11, 1)`, `SEASONS` from the file):

1. `test_ranks_by_model_probability_over_confidence` — picks
   `(5, 18.0, -110, 0.40)`, `(3, 4.0, -110, 0.70)`, `(4, 9.0, -110, 0.55)`;
   expect order `["P1-1", "P1-2", "P1-0"]`.
2. `test_pick_without_probability_sorts_last` — `(5, 9.0, -110, None)`,
   `(2, 3.0, -110, 0.52)`; expect `["P1-1", "P1-0"]`.
3. `test_longshot_past_the_ceiling_is_not_emailed` — `(5, 18.0, 248, 0.34)`,
   `(3, 4.0, -130, 0.60)`; expect `["P1-1"]`.
4. `test_price_at_the_ceiling_is_kept` — `(3, 4.0, 150, 0.45)`; expect
   `["P1-0"]` (boundary is inclusive).
5. `test_missing_price_is_kept` — `(3, 4.0, None, 0.55)`; expect `["P1-0"]`.
6. `test_ceiling_can_be_disabled` — `(5, 18.0, 800, 0.20)` passed with
   `max_odds=None`; expect `["P1-0"]`.
7. `test_ceiling_does_not_touch_props` — `_mk_priced(..., [(3, 10.0, 300, 0.6)], pick_type="prop")`
   and no game picks; expect `sections[0].props` to have one entry and
   `sections[0].picks == []`.

**Mutation check, required before you call these tests a guard**
(repo rule: a test that still passes when the implementation is broken is
not a test): temporarily change `<= max_odds` to `<= 10_000` in the
selector, run the file, confirm test 3 **fails**, then restore. Then delete
`-prob,` from the sort tuple, run, confirm test 1 fails, restore. Record
both outcomes in your final report.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q` → all pass, seven more than before.

### Step 5: Dry-run against production data

Run the dry-run command from "Commands you will need". It writes
`digest_preview.html` and sends nothing.

**Verify**: `grep -o '([+]*[0-9]*)' digest_preview.html | tr -d '()+' | sort -n | tail -1` → a number ≤ 150, or no output if the day's digest is empty (an empty day is not a failure; say so in the report).

## Test plan

- New tests: the seven listed in Step 4, in
  `backend/tests/test_digest_selector.py`, modeled on the existing
  `test_ranks_by_confidence_then_edge`.
- Verification: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py -q` → all pass.

## Done criteria

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0
- [ ] Seven new tests exist in `test_digest_selector.py` and pass
- [ ] Both mutation checks in Step 4 were performed and made the named tests fail
- [ ] `grep -n "max_odds" backend/digest/selector.py backend/digest/job.py config.yaml` shows all three files
- [ ] `git diff --stat 84dc78c..HEAD -- backend/analysis/variants/ensemble.py` is empty
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts.
- `PickModel.model_prob` is missing from `backend/models.py`.
- A mutation check does not make the named test fail after one honest fix
  attempt to the test.
- The dry run shows a game pick above +150 after Step 5 — that means a
  code path other than `select_digest` feeds the email.
- You find yourself wanting to edit `ensemble.py`.

## Maintenance notes

- Plan 020 adds `model_prob` to `DigestPick` and changes the rendered row.
  It must build on this sort order, not reintroduce the edge sort.
- If a per-sport ceiling is ever wanted (baseball favorites are rarely
  shorter than -180, football favorites often -300), make `max_odds` accept
  a mapping of sport → int; keep the integer form working.
- A reviewer should check that the props branch is untouched and that
  `DEFAULT_MAX_ODDS` in `ensemble.py` is still `None`.
- Deferred: re-measure the price-band table monthly. When the effective
  sample per band passes ~60, revisit the ceiling value.
