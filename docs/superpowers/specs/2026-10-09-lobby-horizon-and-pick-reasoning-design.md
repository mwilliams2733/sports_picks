# Lobby horizon and pick reasoning — design

Date: 2026-10-09. Owner requests: "I would like to see betting information for the
next week in the app ... mimic what the professional betting sites do" and "add some
analysis/reasoning for the picks" (model picks and Claude's picks; numbers plus a
written note; build both together).

## Part A — the Lobby looks ahead like a sportsbook

### Problems found (2026-10-09)
- The Lobby asks for `/paper/board?days=7` (`frontend/src/api/client.ts`), so next
  week's games never appear although the server allows `MAX_DAYS = 14`.
- `refresh_prices` (scheduler, every 3h) only refreshes sports with a game **today**
  (`_sports_still_to_play`). NFL had no game Fri/Sat, so from Thu 18:30 ET every NFL
  price was stale and Sunday's 13 games could not be bet. A manual refresh at ~22:00 ET
  (6 credits) fixed it for the weekend: next week's NFL lines are already posted (11 of 13).

### Design
1. **Two weeks.** The Lobby requests 14 days (the server maximum). Empty-board copy says 14.
2. **Every sport on the board stays priced.** `refresh_prices` refreshes each in-season
   sport with a scheduled, not-started game from today through the board horizon (the same
   `MAX_DAYS`, one constant). One Odds API call per sport prices all its posted games.
   Cost today: NFL + MLB added, ~3 credits × 8 runs × 2 = ~48/day (current use ~55-90/day;
   target 600/day, 20,000/month).
3. **Date chips per sport:** All · Today · Tomorrow · This week · Next week. A week runs
   Tuesday-Monday ET (the NFL week; Saturday college games fall inside it). "This week" =
   today through the coming Monday; "Next week" = the Tuesday-Monday after. A chip with no
   games is hidden; the choice resets to All when the sport changes.
4. **Day headers:** "Today", "Tomorrow", then "Sun Oct 18", each with its game count.
5. **Sport tabs in sportsbook order:** nfl, ncaaf, mlb, nba, nhl, ncaab, mma, boxing; any
   other sport after them alphabetically. Only sports with games are shown.
6. A game with no posted price stays on the board with locked prices (unchanged).

## Part B — why each pick was made

### What exists
- `picks.model_prob`, `market_prob_novig`, `edge_pct`, `odds_at_pick`,
  `suggested_unit_size` on every model pick: the real reason (model chance vs the market's
  margin-free chance). The note quotes the stored `edge_pct` as is (vig-adjusted, over
  break-even: `odds_utils.value_edge`), never recomputes it.
- `picks.rationale_json`: structured factor codes rendered to prose ONLY by
  `backend/analysis/rationale.py` ("Rating gap slightly favors KC"). Sparse: since 10-01,
  NFL 7/200, NCAAF 15/215, MMA 0/24 non-withdrawn picks carry any. Shown in the email only.
- Claude's NFL picks: 2-3 sentences of reasoning, a 1-5 confidence and agree/against the
  model, written at pick time (logs-archive/claude-picks-procedure.md), kept in
  `logs-archive/claude-picks-*.json` and emailed. Never in the app.
- Measured (memory, docs/criteria-backlog.md): injuries, weather, travel and rest are
  priced into NFL lines; NFL/MLB shrink weight 0.00 (no edge over the market shown).

### B1 — "Why this pick" for model picks
- The board's `model_pick` (and the game page) gains `reasoning`:
  `{model_prob, market_prob, edge_pct, fair_odds, units, factors: [str], note: str}`.
  `fair_odds` = American odds of `model_prob`; `factors` = the existing rendered factor
  lines; `note` = a written paragraph built ONLY in `rationale.py` from those numbers:
  > The model gives Kansas City a 58% chance to win; the books' price, with their margin
  > removed, says 52%. At -110 that is a 10.7% edge (fair price -138). [numbers illustrative] Rating gap slightly
  > favors Kansas City. The model has not shown an edge over NFL closing lines yet, so treat
  > this as one opinion, not a sure thing.
- The last sentence is a per-sport caveat from one table, worded from measured results
  (NFL/MLB: no demonstrated edge; others: "too few graded picks to judge"). No claim about
  injuries, weather or matchups the model does not use.
- UI: the Lobby's model-pick strip shows "Why?" opening the panel; the game page shows the
  panel under the model's pick. Factors absent -> that line is omitted, never invented.

### B2 — notes on placed bets (Claude's picks)
- A placed straight bet can carry an optional `note` (≤ 600 chars): new nullable column
  `paper_picks.note`; `POST /users/{id}/picks` accepts `note`. Shown on the ticket (My Bets,
  player page) and carried in the feed's placed-bet event, so the league sees Claude's
  reasoning next to the bet (Tail keeps working unchanged).
- The Claude-picks procedure (step 5) sends `note` = the reasoning + "Confidence n/5 ·
  Model: agree|against|no side". Sources stay in the email.
- Backfill: the reasoning in the existing `claude-picks-*.json` files is written onto the
  matching user-3 bets (one-off script, dry run, backup first).
- Friends could send a note through the API too; a note box in the bet slip is NOT in scope.
- Migration: one nullable column -> the live-db procedure (stop scheduler, backup, migrate,
  restart).

## Decision for the owner — MMA picks
23 of 24 published MMA picks since 10-01 have `model_prob = 0.5` exactly: the model has no
information on those fights, so every fighter priced under even money shows as "edge"
(average 19.2% vs 6-12% elsewhere). MMA is one of the three emailed sports. B1 would show
"model 50%" on them. Options: (a) show them as they are; (b) stop publishing picks whose
model probability is the no-information 0.5 (they stay as tracking-only rows, so the record
stays complete); (c) investigate why MMA has no information first, then decide.

**Owner decision (2026-10-09): (c), with (b) in the meantime.** Stopgap: a game pick whose
model probability is exactly the no-information 0.5 is saved as tracking-only (never on the
board, the strip or the email). Existing published 0.5 picks on games not yet started are
moved to tracking-only by a one-off (dry run, backup first); graded history is left as it
was emailed. The investigation reports why MMA has no information and what would fix it;
the fix itself is a separate decision.

## Testing
TDD per task; mutation checks on the guards (refresh horizon, chip boundaries Tue-Mon,
tab order, caveat per sport, note length and HTML-safety, backfill matching only user 3).
Visual check at 375px and desktop on a snapshot (:8001).

## Out of scope
Futures/outrights, live in-game betting, a note box in the slip, LLM-written notes for
model picks, props reasoning (football props are tracking-only).
