# Plan 024: Name the team, not the side

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat d63f816..HEAD -- backend/digest/selector.py backend/digest/render.py backend/tests/test_digest_selector.py backend/tests/test_digest_render.py`
> Expected: empty. On a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW — presentation only. No pick is created, scored, stored or graded differently.
- **Depends on**: plans 017 and 020 (both merged)
- **Category**: dx
- **Planned at**: commit `d63f816`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The label function is nearly literal here, but its fallback behaviour and the tests are written from prose, and one existing test must be inverted rather than deleted. Mid-tier is the floor. Not `haiku`.

## Why this matters

The daily email names the *side of the game* a pick is on, not the team. A
recipient reads:

```
HOME ML (-137) — St. Louis Cardinals @ Pittsburgh Pirates — ★★★★★ Model 70% / Price 58%
```

and has to work out for themselves that "HOME" means Pittsburgh. That is
jargon plus a decoding step, on an email sent to four people who did not
write the system. It reads like a tool's debug output rather than a
recommendation.

The information needed to say it plainly is already loaded — the selector
fetches both `Team` rows to build the matchup string. After this plan the
same pick reads:

```
Pittsburgh Pirates to win (-137)
St. Louis Cardinals at Pittsburgh Pirates · ★★★★★ · Model 70% · Market 58%
```

**Scope discipline.** This is a naming change and nothing else. The owner
asked for the pick names. Three adjacent ideas discussed at the same time are
deliberately **out of scope**: dropping the star rating, renaming the subject
line, and relabelling "Price" as "Market" in the probability pair. Do not do
them. (The word "Market" appears in the sample above only because the owner's
own example used it; leave the rendered word as it currently is — see
"Out of scope".)

## Current state

- `backend/digest/selector.py`
  - `DigestPick` is a frozen dataclass: `sport, matchup, pick_value, odds,
    confidence, edge_pct, rationale, model_prob=None, price_prob=None`.
    Appending fields is safe — `grep -rn "DigestPick(" backend/` shows
    construction only in `selector.py` and the two digest test files, all by
    keyword.
  - `_matchup(session, game)` at line 81 returns `f"{away_name} @ {home_name}"`,
    already having fetched both `Team` rows:

    ```python
    def _matchup(session, game: Game) -> str:
        home = session.get(Team, game.home_team_id)
        away = session.get(Team, game.away_team_id)
        home_name = home.name if home else "Home"
        away_name = away.name if away else "Away"
        return f"{away_name} @ {home_name}"
    ```

  - Two `DigestPick(...)` constructions: game picks at line 217, props at 242.
- `backend/digest/render.py` — uses `p.pick_value` in four places: the game
  row's HTML (line 98) and text (105), and the prop row's HTML (127) and
  text (132).
- `backend/tests/test_digest_selector.py` — has `test_matchup_reads_away_at_home`,
  which asserts the `@` form. This plan changes that form, so the test must be
  **updated to assert the new one**, not deleted.

**The label shapes that exist.** Verified against the production database:

| pick_type | stored `pick_value` | count |
|---|---|---|
| moneyline | `HOME ML`, `AWAY ML` | 179 |
| spread | `HOME -1.5`, `HOME +1.5`, `AWAY -1.5` | 40+ |
| over_under | `Over 7.5`, `Under 226.2`, `Over 52.8` | 200+ |
| prop | `James Harden Over 6.5 Assists` | 54 |

Spreads and totals are **gated off in production today** —
`SPREAD_VALIDATED_SPORTS` and `TOTALS_VALIDATED_SPORTS` in
`backend/analysis/variants/ensemble.py` are both empty frozensets — but
historical picks carry them and the gates are designed to reopen, so the
label function must handle every shape above. Props already name the player
and must pass through untouched.

Conventions to match:
- `render.py` is table-based HTML with inline styles only, always with a
  plain-text twin. Both twins must change together.
- The selector is pure: session and date in, dataclasses out.
- Docstrings explain why a rule exists, with the concrete case that motivated
  it.
- **Never lose information.** An unrecognised `pick_value` must render as
  itself rather than raise or render as an empty string.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Digest tests | `<python> -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py backend/tests/test_check_digest.py -q` | all pass |
| Full suite | `<python> -m pytest backend/tests -q` | all pass (1492 baseline) |
| Dry run | `DIGEST_DRY_RUN=1 DIGEST_DRY_RUN_PATH=digest_preview.html <python> -c "import yaml,datetime;from backend.database import get_engine;from backend.digest.job import send_daily_digest;print(send_daily_digest(yaml.safe_load(open('config.yaml')), get_engine('<abs db path>'), target_date=datetime.date(2026,9,23)))"` | writes the preview, sends nothing |

## Scope

**In scope**:
- `backend/digest/selector.py`
- `backend/digest/render.py`
- `backend/tests/test_digest_selector.py`
- `backend/tests/test_digest_render.py`

**Out of scope** — do not touch, even though they are one line away:
- The star rating. It stays exactly as it is.
- The subject line and the `TOP PICKS` header.
- The rendered word `Price` in the probability pair. Leave it.
- `backend/digest/job.py`, `config.yaml`, and anything under
  `backend/analysis/` or `backend/pipeline/`. No pick changes how it is
  generated, scored or stored.
- The prop rows' own label. Props already name the player.

## Git workflow

- Conventional commit, e.g. `feat(digest): name the team a pick is on, not the side`
- Do NOT push or open a PR.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Steps

### Step 1: Carry the two team names on `DigestPick`

In `selector.py`, append two fields (keyword construction everywhere, so
appending is safe):

```python
    home_team: str | None = None
    away_team: str | None = None
```

Add a helper beside `_matchup` that returns both names, and make `_matchup`
use it so there is one place that resolves a team name:

```python
def _team_names(session, game: Game) -> tuple[str, str]:
    """(away_name, home_name), with the same fallbacks `_matchup` has used.

    One resolver, because the rendered label and the matchup line must agree:
    an email that says "Pittsburgh Pirates to win" above "Cardinals at Home"
    would be worse than the jargon it replaced.
    """
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    return (away.name if away else "Away", home.name if home else "Home")
```

`_matchup` becomes a one-liner over it, and **changes its separator from
`@` to ` at `**:

```python
def _matchup(session, game: Game) -> str:
    away_name, home_name = _team_names(session, game)
    return f"{away_name} at {home_name}"
```

Populate `home_team` / `away_team` on **both** `DigestPick(...)`
constructions — the prop one too, so a future prop label can use them.

**Verify**: `<python> -m pytest backend/tests/test_digest_selector.py -q` → `test_matchup_reads_away_at_home` FAILS (it asserts the `@` form). That failure is expected here and is fixed in Step 3. Every other test passes.

### Step 2: The label function in the renderer

In `render.py`, add:

```python
#: Words for what a totals line counts, by sport. Absent -> no unit word,
#: which is correct for a sport nobody has checked rather than a guess.
_TOTAL_UNITS = {
    "mlb": "runs", "nfl": "points", "ncaaf": "points",
    "nba": "points", "ncaab": "points",
}


def _selection_label(p: DigestPick) -> str:
    """What the pick is, in words a reader does not have to decode.

    `pick_value` is stored as the SIDE of the game -- "HOME ML", "AWAY +1.5"
    -- which is the right thing to store and the wrong thing to email: the
    reader has to work out which team is home. The team names travel on the
    DigestPick for exactly this.

    Anything unrecognised falls through to the stored value unchanged. A
    label this function cannot parse is still information; swallowing it
    would turn a readable oddity into a blank line.
    """
    value = p.pick_value
    side, _, rest = value.partition(" ")
    team = {"HOME": p.home_team, "AWAY": p.away_team}.get(side)

    if team:
        if rest == "ML":
            return f"{team} to win"
        if rest:
            return f"{team} {rest}"       # spread: "Pirates -1.5"
        return team

    if side in ("Over", "Under") and rest:
        unit = _TOTAL_UNITS.get(p.sport)
        return f"{side} {rest} {unit}" if unit else value

    return value
```

Replace `p.pick_value` with `_selection_label(p)` in the **game-pick** HTML
row and its text line only. Leave both **prop** uses as `p.pick_value`.

**Verify**: `grep -n "p.pick_value" backend/digest/render.py` → exactly two matches, both inside the props block.

### Step 3: Tests

`backend/tests/test_digest_selector.py`:

1. **Update** `test_matchup_reads_away_at_home` to assert the ` at ` form
   rather than `@`. Keep the test name and its intent; only the expected
   string changes.
2. `test_digest_pick_carries_both_team_names` — assert a game pick's
   `home_team` and `away_team` match the seeded `Team.name` values.
3. `test_a_missing_team_row_falls_back_without_crashing` — seed a game whose
   `home_team_id` points at no `Team` row; assert `home_team == "Home"` and
   the section still renders.

`backend/tests/test_digest_render.py` — build `DigestPick`s directly:

4. `test_home_moneyline_names_the_home_team` — `pick_value="HOME ML"`,
   `home_team="Pittsburgh Pirates"`; assert `"Pittsburgh Pirates to win"` in
   both html and text, and `"HOME ML"` in neither.
5. `test_away_moneyline_names_the_away_team` — `pick_value="AWAY ML"`,
   `away_team="Los Angeles Angels"`; assert `"Los Angeles Angels to win"`.
6. `test_a_spread_keeps_its_line_beside_the_team` — `pick_value="HOME -1.5"`,
   `home_team="Pittsburgh Pirates"`; assert `"Pittsburgh Pirates -1.5"`.
7. `test_a_total_gains_its_sport_unit` — `pick_value="Over 7.5"`,
   `sport="mlb"`; assert `"Over 7.5 runs"`.
8. `test_a_total_in_an_unlisted_sport_keeps_its_bare_line` —
   `pick_value="Over 7.5"`, `sport="boxing"`; assert `"Over 7.5"` appears and
   no unit word was invented.
9. `test_an_unrecognised_label_survives_unchanged` — `pick_value="SOMETHING ODD"`
   with both team names set; assert `"SOMETHING ODD"` appears verbatim.
10. `test_a_pick_without_team_names_falls_back_to_the_stored_value` —
    `pick_value="HOME ML"` with `home_team=None`; assert `"HOME ML"` appears
    rather than a blank or a crash.
11. `test_prop_labels_are_untouched` — a prop whose `pick_value` is
    `"James Harden Over 6.5 Assists"`; assert it appears verbatim.

**Mutation check, required** (repo rule: a test that still passes when the
implementation is broken is not a test): make `_selection_label` return
`p.pick_value` unconditionally as its first line; tests 4, 5, 6 and 7 must
fail. Restore and report which failed and how.

**Verify**: `<python> -m pytest backend/tests/test_digest_selector.py backend/tests/test_digest_render.py -q` → all pass, ten more than before (one updated, ten added).

### Step 4: Dry run against real data

Run the dry-run command for `2026-09-23` (a date with picks) and paste the
plain-text body into your report.

**Verify**: no game-pick line contains `HOME ML`, `AWAY ML`, `HOME `, or `AWAY `; every game-pick line names a real team; the matchup line reads "X at Y".

### Step 5: Full suite

**Verify**: `<python> -m pytest backend/tests -q` → all pass. Report exact counts against the 1492 baseline.

## Done criteria

- [ ] Full suite exits 0, count ≥ 1502
- [ ] `grep -c "p.pick_value" backend/digest/render.py` → `2` (props only)
- [ ] `grep -n "_selection_label" backend/digest/render.py` → definition plus two call sites
- [ ] `grep -n " at \| @ " backend/digest/selector.py` shows `_matchup` using ` at `
- [ ] `git diff --stat d63f816..HEAD -- backend/digest/job.py config.yaml backend/analysis backend/pipeline` is empty
- [ ] The rendered star row and the word `Price` are unchanged (`grep -n "_stars\|Price" backend/digest/render.py` matches the pre-change lines)
- [ ] Mutation check performed, failing test names reported
- [ ] Dry-run body pasted, containing no `HOME`/`AWAY` in a game-pick line
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- `DigestPick` is constructed positionally anywhere
  (`grep -rn "DigestPick(" backend/`) — appending fields would be unsafe.
- A `pick_value` shape appears in the database that none of the four rows in
  "Current state" covers, and it is not a prop.
- `test_matchup_reads_away_at_home` cannot be updated without changing other
  tests — that would mean the `@` form is load-bearing somewhere this plan
  did not map.
- Any test outside the four in-scope files fails.
- You are tempted to touch the stars, the subject line, or the word `Price`.

## Maintenance notes

- `_TOTAL_UNITS` deliberately omits boxing and mma. Their totals are rounds,
  not points, and nobody has checked what those markets actually mean here —
  an omitted sport renders the bare line, which is honest.
- If the spread or totals gates in `ensemble.py` ever reopen, these labels
  start appearing in real emails for the first time. Re-read the dry run then;
  the shapes are covered by tests but have never been seen by a recipient.
- The owner declined three adjacent changes in the same conversation: drop
  the stars, rename the subject, relabel `Price` as `Market`. They are not
  rejected on merit, only deferred. If they come back, the stars are the one
  with a substantive argument behind it — the tiers map to expected win rates
  (`recalibrator.EXPECTED_WIN_RATES`) that have never been verified, because
  `calibration_history` is still empty.
- A reviewer should check the prop rows are byte-identical and that no pick
  generation path changed.
