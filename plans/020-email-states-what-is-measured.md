# Plan 020: The email states what is measured, not an edge

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 84dc78c..HEAD -- backend/digest/selector.py backend/digest/render.py backend/digest/job.py config.yaml backend/tests/test_digest_selector.py backend/tests/test_digest_render.py`
> Plan 017 is EXPECTED to have changed `selector.py`, `job.py`, `config.yaml`
> and `test_digest_selector.py`. Confirm those changes are 017's (a
> `max_odds` argument and a `model_prob`-first sort key) and nothing else.
> Any other drift is a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/017-digest-ranks-by-win-probability.md (DONE first)
- **Category**: bug
- **Planned at**: commit `84dc78c`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The render changes are described, not transcribed (the header row, the footer sentence, HTML and text twins), the `_grade` test helper is left to the executor, and twelve tests are written from prose. The largest of the four plans by turn count. Mid-tier floor; not `haiku`.

## Why this matters

Every game pick in the email carries "+X%", the model's claimed edge over
the market. Three separate measurements in this repo say that number is not
real: the market-shrinkage fit (`backend/analysis/market_shrinkage.py`
docstring) found the model's optimal weight against the price is **0.00**
held out; the EPA experiment (`backend/scripts/epa_experiment.py`) found no
team-level football feature beats the closing line; and the graded book
shows the largest claimed edges losing most. The stars map to expected win
rates of 70/63/57/53/50 (`recalibrator.EXPECTED_WIN_RATES`) that have never
been checked, because `calibration_history` is empty — the recalibrator has
never had a large enough sample to fire.

The email goes to four people. It should show them the two things that are
actually known: what the model thinks the win probability is, what the
price implies, and how this sport's picks have actually done recently.
After this plan the "+X%" line is gone from game picks, replaced by
"Model 64% · Price 58%", and each sport's header carries its trailing
30-day record. A sport whose trailing record is below a configurable floor
can be suppressed; that switch ships **off** by default.

## Current state

- `backend/digest/selector.py` — after plan 017: `DigestPick` (frozen
  dataclass, fields `sport, matchup, pick_value, odds, confidence, edge_pct,
  rationale`), `DigestSection(sport, picks, props)`, and `select_digest(...)`
  with `max_odds`.
- `backend/digest/render.py` — `render_digest(sections, target_date)` →
  `(subject, html, text)`. Game-pick row at lines 79-90; the "+X%" is the
  text `+{p.edge_pct}%` in both the HTML row and the plain-text line.
  Prop rows (lines 97-116) deliberately omit any percentage — keep that.
- `backend/digest/job.py` — passes config into `select_digest`.
- `backend/models.py` — `PickModel.model_prob` (Float, nullable),
  `PickModel.odds_at_pick` (Integer, nullable); `PickResult(pick_id, result,
  payout, ...)` with `result` in `win | loss | push`; `Game.date` (Date),
  `Game.sport`.
- `backend/analysis/odds_utils.py:30` — `american_to_implied_prob(odds: int) -> float`
  (raw, includes vig; raises `InvalidOddsError` on a price in (-100, 100)).
- `backend/tests/test_digest_render.py` and `test_digest_selector.py` —
  the patterns. `test_prop_row_omits_the_percent_edge_glyph` asserts a prop's
  edge number does not appear anywhere in the output; do not break it.

The game-pick HTML row today, `render.py:79-90`:

```python
            rows.append(
                f'<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
                f'<div style="{_FONT}font-size:15px;font-weight:600;color:#111827;">'
                f'{_escape(p.pick_value)} <span style="font-weight:400;color:#6b7280;">({p.odds})</span></div>'
                f'<div style="{_FONT}font-size:13px;color:#374151;padding-top:2px;">'
                f'{_escape(p.matchup)} &nbsp;·&nbsp; {_stars(p.confidence)} &nbsp;·&nbsp; +{p.edge_pct}%</div>'
                f'{rationale_html}</td></tr>'
            )
            text_lines.append(f"  {p.pick_value} ({p.odds}) — {p.matchup} — {_stars(p.confidence)} +{p.edge_pct}%")
```

The sport header row, `render.py:69-74`, prints only `_label(section.sport)`.

Conventions: `render.py` is table-based HTML with inline styles only, no
CSS, no fonts, no JS, and always produces a plain-text twin. The selector is
pure (session + date in, dataclasses out). Docstrings state the incident
that motivated a rule.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Digest tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py backend/tests/test_check_digest.py -q` | all pass |
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | all pass |
| Dry run | `DIGEST_DRY_RUN=1 .venv/Scripts/python.exe -c "import yaml;from backend.database import get_engine;from backend.digest.job import send_daily_digest;print(send_daily_digest(yaml.safe_load(open('config.yaml')), get_engine('sports_picks.db')))"` | writes `digest_preview.html`, sends nothing |

## Scope

**In scope**:
- `backend/digest/selector.py`
- `backend/digest/render.py`
- `backend/digest/job.py`
- `config.yaml` (one optional key under `digest:`)
- `backend/tests/test_digest_selector.py`
- `backend/tests/test_digest_render.py`

**Out of scope**:
- `backend/digest/sender.py` — transport is unchanged.
- `backend/scripts/check_digest.py` — reads the log and the picks table,
  not the email body.
- The prop rows and the prop query — unchanged.
- `backend/analysis/recalibrator.py` — the stars' expected rates are a
  separate question; this plan stops *asserting* them, it does not fix them.
- `frontend/` — the web UI shows edge in its own way; not part of the email.

## Git workflow

- Branch: `advisor/020-email-states-what-is-measured`
- Conventional commit, e.g. `feat(digest): show model vs price and the trailing record, drop the edge`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Carry the probabilities on `DigestPick`

In `selector.py`, extend the dataclass (append fields so positional
construction in existing tests keeps working):

```python
@dataclass(frozen=True)
class DigestPick:
    sport: str
    matchup: str
    pick_value: str
    odds: int
    confidence: int
    edge_pct: float
    rationale: str
    model_prob: float | None = None
    price_prob: float | None = None
```

Add a helper:

```python
def _price_prob(odds: int | None) -> float | None:
    """What the quoted price implies, vig included, or None without a price.

    Raw rather than de-vigged on purpose: the email shows one side, and a
    reader can check a raw implied probability against the price by hand.
    """
    if odds is None:
        return None
    try:
        return american_to_implied_prob(odds)
    except InvalidOddsError:
        return None
```

with `from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob`
at the top. In the **game-pick** `DigestPick(...)` construction, add
`model_prob=p.model_prob, price_prob=_price_prob(p.odds_at_pick)`. Leave the
prop construction alone (its fields default to `None`).

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py -q` → all pass.

### Step 2: Carry the trailing record on `DigestSection`

Extend the section:

```python
@dataclass(frozen=True)
class DigestSection:
    sport: str
    picks: list[DigestPick]
    props: list[DigestPick]
    record: tuple[int, int] | None = None   # (wins, losses) over the trailing window
```

Add to `selector.py`:

```python
TRAILING_DAYS = 30


def trailing_record(session, sport: str, target_date, days: int = TRAILING_DAYS):
    """(wins, losses) for this sport's graded game picks in the last `days`.

    Decided results only: a push is neither. Props are excluded because they
    are graded on a different scale and listed separately. None when nothing
    has been graded, which is different from 0-0 -- a sport with no history
    must not print a record.
    """
    cutoff = target_date - timedelta(days=days)
    rows = (session.query(PickResult.result)
            .join(PickModel, PickModel.id == PickResult.pick_id)
            .join(Game, Game.id == PickModel.game_id)
            .filter(Game.sport == sport,
                    Game.date >= cutoff, Game.date < target_date,
                    PickModel.pick_type != "prop",
                    PickResult.result.in_(("win", "loss")))
            .all())
    if not rows:
        return None
    wins = sum(1 for (r,) in rows if r == "win")
    return wins, len(rows) - wins
```

Imports: `timedelta` from `datetime`, `PickResult` from `backend.models`.
In `select_digest`, compute `record = trailing_record(session, sport, target_date)`
and pass it into `DigestSection(...)`.

Add an optional gate, argument `min_trailing_win_pct: float | None = None`
on `select_digest`, applied **after** the record is computed and only when
the record has at least `min_trailing_picks: int = 20` decided picks:

```python
        if (min_trailing_win_pct is not None and record is not None
                and sum(record) >= min_trailing_picks
                and record[0] / sum(record) < min_trailing_win_pct):
            logger.info("Digest: %s suppressed, trailing %d-%d below %.0f%%",
                        sport, record[0], record[1], min_trailing_win_pct * 100)
            continue
```

(add `import logging; logger = logging.getLogger(__name__)`). Thread both
from `job.py`: `min_trailing_win_pct=cfg.get("min_trailing_win_pct")`,
`min_trailing_picks=cfg.get("min_trailing_picks", 20)`. In `config.yaml`
add, commented, under `digest:`:

```yaml
  # Optional. When set (e.g. 0.50), a sport whose trailing 30-day record is
  # below this win rate on at least min_trailing_picks decided picks is left
  # out of the email. Off by default: with five graded NFL picks a record
  # is noise, and the reader can see the record in the header either way.
  # min_trailing_win_pct: 0.50
  # min_trailing_picks: 20
```

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q` → all pass.

### Step 3: Render it

In `render.py`, add:

```python
def _pct(p: float | None) -> str:
    return "—" if p is None else f"{round(p * 100)}%"


def _record(section: DigestSection) -> str:
    if section.record is None:
        return ""
    w, l = section.record
    return f"last 30 days {w}-{l}"
```

Change the sport header row to append the record in the same muted style,
e.g. `NFL &nbsp;·&nbsp; last 30 days 3-2` in HTML and
`-- NFL -- last 30 days 3-2` in text; omit the separator when `_record`
returns "".

Replace `+{p.edge_pct}%` in the game-pick HTML row and text line with
`Model {_pct(p.model_prob)} &nbsp;·&nbsp; Price {_pct(p.price_prob)}` (HTML)
and `Model {_pct(p.model_prob)} / Price {_pct(p.price_prob)}` (text). Keep
the stars. Do not touch the prop rows. Update the footer sentence to:
`Model output for research, not betting advice. "Model" is the model's win
probability; "Price" is what the quoted price implies.`

**Verify**: `grep -n "edge_pct" backend/digest/render.py` → no matches.

### Step 4: Tests

`backend/tests/test_digest_selector.py` (use the `_mk_priced` helper from
plan 017; add a `_grade(session, pick_id, result)` helper that inserts a
`PickResult`):

1. `test_digest_pick_carries_model_and_price_probability` — one pick
   `(3, 4.0, -150, 0.62)`; assert `picks[0].model_prob == 0.62` and
   `abs(picks[0].price_prob - 0.6) < 1e-9`.
2. `test_missing_price_gives_no_price_probability` — odds `None`; assert
   `price_prob is None`.
3. `test_trailing_record_counts_decided_game_picks_only` — three graded
   picks on games dated 10 days before target: win, loss, push; one graded
   prop win; one win on a game 40 days before; assert the section's
   `record == (1, 1)`.
4. `test_no_graded_history_gives_no_record` — assert `record is None`.
5. `test_trailing_record_excludes_today` — a win graded on a game dated
   `target_date` itself is not counted (the window is `< target_date`).
6. `test_suppression_is_off_by_default` — 25 graded losses in the window,
   one live pick; default call still returns the section.
7. `test_suppression_needs_the_minimum_sample` — 5 losses, `min_trailing_win_pct=0.5`;
   section still returned.
8. `test_suppression_fires_below_the_floor` — 15 losses and 5 wins,
   `min_trailing_win_pct=0.5`; `select_digest` returns `[]`.

`backend/tests/test_digest_render.py`:

9. `test_game_row_shows_model_and_price_not_edge` — a `DigestPick` with
   `edge_pct=8.1, model_prob=0.64, price_prob=0.58`; assert `"Model 64%"`
   and `"Price 58%"` in both html and text, and `"8.1"` in neither.
10. `test_missing_probabilities_render_as_dash` — `model_prob=None`; assert
    `"Model —"` in text.
11. `test_section_header_shows_trailing_record` — `DigestSection(..., record=(3, 2))`;
    assert `"last 30 days 3-2"` in html and text.
12. `test_section_without_record_has_no_record_text` — `record=None`;
    assert `"last 30 days"` not in text.

**Mutation check, required**: (a) change `< target_date` to `<= target_date`
in `trailing_record`; test 5 must fail. (b) Put `+{p.edge_pct}%` back into
the text line; test 9 must fail. Restore both and report.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py -q` → all pass, twelve more than before.

### Step 5: Dry run

Run the dry-run command and open `digest_preview.html`.

**Verify**: `grep -c "Model [0-9]*%" digest_preview.html` → equals the number of game picks in the preview (or `0` on an empty day; report which), and `grep -c "+[0-9.]*%" digest_preview.html` → `0`.

## Test plan

- Twelve tests as listed in Step 4, split across the two existing digest
  test files, modeled on the tests already there.
- Verification: the Digest tests command → all pass.

## Done criteria

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0
- [ ] `grep -n "edge_pct" backend/digest/render.py` → no matches
- [ ] `grep -n "trailing_record\|price_prob\|model_prob" backend/digest/selector.py` shows all three
- [ ] `test_prop_row_omits_the_percent_edge_glyph` still passes
- [ ] Both mutation checks performed and reported
- [ ] `config.yaml` has the two commented-out keys and `digest.enabled` is unchanged
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back if:

- Plan 017 is not DONE (its sort key and `max_odds` are absent from
  `selector.py`) — do 017 first, do not fold it in.
- `DigestPick` is constructed positionally anywhere outside the two test
  files (`grep -rn "DigestPick(" backend/ frontend/`) — appending fields
  would then be unsafe.
- `PickResult.result` uses values other than `win`, `loss`, `push`
  (check `grep -n "result=" backend/pipeline/grader.py`).
- The dry run still shows a `+X%` on a game pick.

## Maintenance notes

- The record is per sport across all pick types except props. If spreads or
  totals are re-enabled for a sport (`SPREAD_VALIDATED_SPORTS` /
  `TOTALS_VALIDATED_SPORTS` in `ensemble.py`), the record will mix markets;
  consider splitting it by `pick_type` then.
- The 30-day window is calendar days, the same shape the recalibrator
  rejected for itself (`recalibrator.py` docstring on `MAX_PICKS_PER_TIER`).
  For an email header that is fine — it answers "how has this gone lately"
  — but do not reuse `trailing_record` for anything that sizes a bet.
- Stars remain in the email. Their expected rates are unverified; the
  reviewer should decide whether to keep them once the trailing record is
  visible. Removing them is a one-line change in `render.py`.
- Deferred: showing the de-vigged fair probability instead of the raw
  price probability would need both sides' prices on the pick row, which
  `PickModel` does not store.
