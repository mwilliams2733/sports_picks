# Lobby Horizon and Pick Reasoning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Lobby shows two weeks of bettable games the way a sportsbook does (fresh prices for every sport, date chips, day headers, NFL-first tabs), every model pick explains itself, Claude's bets carry their written reasoning, and no-information picks stop being published while MMA is investigated.

**Architecture:** Backend: `refresh_prices` widens from "sports playing today" to "sports with a game on the board" (one constant, `board.MAX_DAYS`); the pick generator marks a pick at exactly the no-information 0.5 as tracking-only; `rationale.py` (the only home of pick prose) gains `pick_note`, and the board's `model_pick` carries a `reasoning` object; paper bets gain a nullable `note` column carried to tickets and the feed. Frontend: date-chip helpers in `lib/board.ts`, a `PickReasoning` panel used by the Lobby strip and the game page, notes on tickets and feed items. Two one-off scripts (demote no-information picks; backfill Claude's notes) run at merge, dry run first.

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, APScheduler, pytest; React 19, react-query 5, vitest, TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-09-lobby-horizon-and-pick-reasoning-design.md` (owner decisions recorded in it: build both parts; MMA = investigate (c) with stopgap (b)).

## Global Constraints

- Prose about why a pick was made lives ONLY in `backend/analysis/rationale.py` (its module rule). Nothing may claim injuries, weather, matchups or anything the model did not compute.
- Every public reader of picks uses `PickModel.published()` (memory: spread/total prices). Tracking-only picks never reach the board, strip or email.
- `edge_pct` is quoted as stored (vig-adjusted, over break-even); never recomputed.
- Odds API cost is real: 3 credits per sport per call (1 for boxing/mma); budget `odds_budget` in config.yaml (daily_target 600, monthly 20000).
- Styles go in `frontend/src/sportsbook.css`. Money/odds via `formatMoney` / `signedMoney` / `formatOdds`. Negative numbers use U+2212 where `signedMoney` does.
- Layout works at 375px with no horizontal scroll.
- Backend tests: `.venv/Scripts/python -m pytest -q` from the repo root (a worktree uses the MAIN venv: `/c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python`). Frontend: `sh -c "cd frontend && npx vitest run && npx tsc -b"`; a fresh worktree needs `cd frontend && npm ci --legacy-peer-deps`.
- After any mutation test of Python code delete `__pycache__` for that module after the write AND after the restore (memory: mutation .pyc trap).
- Write regex/backslash code with the Write/Edit tools, never bash heredocs.
- Never print the PIN in `.claude-paper-pin`, nor any value from `C:\Users\mwill\.secrets\shared.env`.
- Commit only named files; never `config.yaml`. Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Week boundaries on the edge days:** today a Monday (this week = today only), a Sunday, a Tuesday (this week runs to next Monday) — chips must partition correctly, never drop or double a day. Pinned in Task 2.
2. **A bet note with markup or at the size limit:** shown as plain text (React escapes), 1000 chars accepted, 1001 refused with 422, blank stored as nothing. Pinned in Tasks 6 and 7.
3. **Tailing a bet that has a note:** the Tail request must not carry the note (it is the bettor's, and the strict request model would 422 any unknown key in a parlay leg). Pinned in Task 6.
4. **A model pick with missing numbers** (no `model_prob`, no `market_prob_novig`, no odds, no factors): the panel omits what is missing — never "NaN%", "null", or an invented factor. Pinned in Tasks 4 and 5.
5. **Refresh horizon edges:** a game on the last board day is refreshed, one a day past it is not; a started game, a finished game and an out-of-season sport are not. Pinned in Task 1.

## Design notes (where the plan is more specific than the spec)

- The spec listed `factors: [str]` in the reasoning payload; the note already contains the rendered factors, so the payload drops the separate list (YAGNI; one source of the words).
- The no-information stopgap applies to every game pick at exactly 0.5 (not only MMA): a 0.5 pick's "edge" is the price alone in any sport. Props are a separate pipeline and untouched.
- The game-page panel labels the pick with the frontend's existing `resolveLabel(pick_value, home, away)`.

---

### Task 1: Price refresh covers every sport on the board

**Files:**
- Modify: `backend/pipeline/scheduler.py` (`refresh_prices` ~242-279, `_sports_still_to_play` ~282-300)
- Test: `backend/tests/test_price_refresh.py`

**Interfaces:**
- Consumes: `backend.paper.board.MAX_DAYS` (14).
- Produces: `_sports_with_games_ahead(session, config: dict, first: date, now: datetime, days: int = MAX_DAYS) -> list[str]` (replaces `_sports_still_to_play`, whose only caller is `refresh_prices`).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_price_refresh.py`, replace the test `test_refreshes_in_season_sports_with_a_game_still_to_play_today` with the block below, and add `from backend.paper.board import MAX_DAYS` to the imports.

```python
def test_refreshes_every_in_season_sport_with_a_game_still_to_play_on_the_board(engine, calls):
    sch.refresh_prices(_config(), engine)
    # mlb's game today is final but it plays tomorrow (on the board); nba is
    # out of season. Before 2026-10-09 only sports playing TODAY were
    # refreshed, so NFL went stale every Friday and Saturday.
    assert [sorted(s) for s in calls["odds"]] == [["mlb", "mma", "nfl"]]


def _only_mlb_game_on(engine, day, status="scheduled"):
    s = get_session(engine)
    s.query(Game).filter(Game.sport == "mlb").delete()
    s.add_all([Team(id=101, name="MH", abbreviation="MH", sport="mlb"),
               Team(id=102, name="MA", abbreviation="MA", sport="mlb")])
    s.flush()
    s.add(Game(sport="mlb", season="2026", date=day, status=status,
               home_team_id=101, away_team_id=102))
    s.commit()
    s.close()


def test_a_game_on_the_last_board_day_is_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY + datetime.timedelta(days=MAX_DAYS - 1))
    sch.refresh_prices(_config(), engine)
    assert "mlb" in calls["odds"][0]


def test_a_game_past_the_board_horizon_is_not_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY + datetime.timedelta(days=MAX_DAYS))
    sch.refresh_prices(_config(), engine)
    assert [sorted(s) for s in calls["odds"]] == [["mma", "nfl"]]


def test_a_game_from_before_today_is_not_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY - datetime.timedelta(days=1))
    sch.refresh_prices(_config(), engine)
    assert "mlb" not in calls["odds"][0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_price_refresh.py -q`
Expected: FAIL — `test_refreshes_every_in_season_sport…` gets `[['mma', 'nfl']]`; `test_a_game_on_the_last_board_day_is_refreshed` fails (`'mlb' in ['mma','nfl']` false). The other two pass already (they pin the boundary).

- [ ] **Step 3: Implement**

In `backend/pipeline/scheduler.py`, add near the other imports:

```python
from backend.paper.board import MAX_DAYS
```

Replace the whole function `_sports_still_to_play` with:

```python
def _sports_with_games_ahead(session, config: dict, first: date, now: datetime,
                             days: int = MAX_DAYS) -> list[str]:
    """In-season sports with a scheduled game that has not started, from
    ``first`` through the board's horizon (``days`` -- the Lobby's two weeks).

    Was today only (`_sports_still_to_play`) until 2026-10-09: NFL had no game
    on Friday or Saturday, so every NFL price went stale Thursday night and
    Sunday's games could not be bet (owner: "I would like to see betting
    information for the next week"). One Odds API call per sport prices all
    of that sport's posted games, so the horizon adds sports, not calls per
    game. A game still "scheduled" after it started (grading flips it the next
    morning) does not count; a game with no start time does, the project-wide
    convention (`pricing.open_for_betting`).
    """
    from datetime import timedelta
    from backend.time_utils import game_start_utc
    active = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"])]
    last = first + timedelta(days=days)
    live = set()
    for game in (session.query(Game)
                 .filter(Game.sport.in_(active), Game.date >= first, Game.date < last,
                         Game.status == "scheduled").all()):
        start = game_start_utc(game)
        if start is None or start > now:
            live.add(game.sport)
    return [s for s in active if s in live]
```

In `refresh_prices`, replace

```python
        sports = _sports_still_to_play(session, config, et_today(),
                                       datetime.now(timezone.utc))
```

with

```python
        sports = _sports_with_games_ahead(session, config, et_today(),
                                          datetime.now(timezone.utc))
```

and in its docstring replace the sentence starting "This runs every `PRICE_REFRESH_HOURS` for each in-season sport with a game still scheduled today;" through "against a 600 daily target." with:

```
    This runs every `PRICE_REFRESH_HOURS` for each in-season sport with a game
    still to play on the board (`_sports_with_games_ahead`, two weeks); one
    Odds API call per sport refreshes all of that sport's posted games.
    Measured 2026-10-02 from requests_remaining: 3 credits a call (1 for
    boxing/mma, moneyline only), so at most ~5 sports x 8 runs x 3 = 120 a
    day against a 600 daily target.
```

Check nothing else calls the old name: `grep -rn "_sports_still_to_play" backend` → no output.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest backend/tests/test_price_refresh.py backend/tests/test_scheduler.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation check**

Change `Game.date < last` to `Game.date <= last` → `test_a_game_past_the_board_horizon_is_not_refreshed` FAILS. Restore. Change `Game.date >= first` to nothing (delete that filter argument) → `test_a_game_from_before_today_is_not_refreshed` FAILS. Restore. Delete `backend/pipeline/__pycache__` after each write and restore.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/scheduler.py backend/tests/test_price_refresh.py
git commit -m "fix(prices): refresh every sport with a game on the board, not only sports playing today

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Two weeks, date chips, day headers and sportsbook tab order

**Files:**
- Modify: `frontend/src/lib/board.ts` (`groupByDay`, `sportTabs`; new chip helpers)
- Modify: `frontend/src/lib/board.test.ts`
- Modify: `frontend/src/pages/Lobby.tsx`
- Modify: `frontend/src/pages/Lobby.test.tsx`
- Modify: `frontend/src/api/client.ts` (board `days=14`)
- Modify: `frontend/src/sportsbook.css`

**Interfaces:**
- Produces: `SPORT_ORDER: string[]`; `type DateChip = 'all' | 'today' | 'tomorrow' | 'this_week' | 'next_week'`; `DATE_CHIPS: Record<DateChip, string>`; `dateChips(games: BoardGame[], today: string): DateChip[]`; `filterByChip(games: BoardGame[], chip: DateChip, today: string): BoardGame[]`. `groupByDay` keeps its shape; the day after today is labelled "Tomorrow".

- [ ] **Step 1: Write the failing helper tests**

In `frontend/src/lib/board.test.ts`: add `dateChips, filterByChip` to the import from `./board`; replace the `'labels today "Today" and keeps board order within a day'` test and the `sportTabs` describe with:

```ts
  it('labels today "Today", the next day "Tomorrow", and keeps board order within a day', () => {
    const a = game({ id: 1, date: '2026-10-10' })
    const b = game({ id: 2, date: '2026-10-11' })
    const c = game({ id: 3, date: '2026-10-11' })
    const d = game({ id: 4, date: '2026-10-12' })
    expect(groupByDay([a, b, c, d], '2026-10-10')).toEqual([
      { date: '2026-10-10', label: 'Today', games: [a] },
      { date: '2026-10-11', label: 'Tomorrow', games: [b, c] },
      { date: '2026-10-12', label: 'Mon Oct 12', games: [d] },
    ])
  })
})

describe('sportTabs', () => {
  it('orders sports the way a US sportsbook does, then any others alphabetically', () => {
    const tabs = sportTabs(['boxing', 'mma', 'nba', 'curling', 'nfl', 'ncaaf', 'bowls', 'nfl']
      .map(sport => game({ sport })))
    expect(tabs).toEqual(['nfl', 'ncaaf', 'nba', 'mma', 'boxing', 'bowls', 'curling'])
  })
})

describe('date chips (weeks run Tuesday-Monday ET)', () => {
  const on = (date: string) => game({ id: Number(date.slice(-2)), date })
  const ids = (gs: BoardGame[]) => gs.map(g => g.date)
  // Friday 2026-10-09: this week runs to Monday 10-12, next week is Tue 10-13 to Mon 10-19.
  const fri = '2026-10-09'
  const slate = ['2026-10-09', '2026-10-10', '2026-10-12', '2026-10-13', '2026-10-19', '2026-10-20'].map(on)

  it('splits a Friday board into today, tomorrow, this week and next week (Review Focus 1)', () => {
    expect(ids(filterByChip(slate, 'today', fri))).toEqual(['2026-10-09'])
    expect(ids(filterByChip(slate, 'tomorrow', fri))).toEqual(['2026-10-10'])
    expect(ids(filterByChip(slate, 'this_week', fri))).toEqual(['2026-10-09', '2026-10-10', '2026-10-12'])
    expect(ids(filterByChip(slate, 'next_week', fri))).toEqual(['2026-10-13', '2026-10-19'])
    expect(filterByChip(slate, 'all', fri)).toEqual(slate)
  })
  it('on a Monday, this week is just today and next week starts tomorrow (Review Focus 1)', () => {
    const mon = '2026-10-12'
    const board = ['2026-10-12', '2026-10-13', '2026-10-19', '2026-10-20'].map(on)
    expect(ids(filterByChip(board, 'this_week', mon))).toEqual(['2026-10-12'])
    expect(ids(filterByChip(board, 'next_week', mon))).toEqual(['2026-10-13', '2026-10-19'])
  })
  it('on a Sunday, this week runs through Monday (Review Focus 1)', () => {
    const sun = '2026-10-11'
    const board = ['2026-10-11', '2026-10-12', '2026-10-13'].map(on)
    expect(ids(filterByChip(board, 'this_week', sun))).toEqual(['2026-10-11', '2026-10-12'])
    expect(ids(filterByChip(board, 'next_week', sun))).toEqual(['2026-10-13'])
  })
  it('on a Tuesday, this week runs to the following Monday (Review Focus 1)', () => {
    const tue = '2026-10-13'
    const board = ['2026-10-13', '2026-10-19', '2026-10-20'].map(on)
    expect(ids(filterByChip(board, 'this_week', tue))).toEqual(['2026-10-13', '2026-10-19'])
    expect(ids(filterByChip(board, 'next_week', tue))).toEqual(['2026-10-20'])
  })
  it('offers All plus only the chips that have games, in order', () => {
    expect(dateChips(['2026-10-11', '2026-10-18'].map(on), fri)).toEqual(['all', 'this_week', 'next_week'])
    expect(dateChips([], fri)).toEqual(['all'])
  })
})
```

(The replaced `groupByDay` test ended the `describe('formatDay / groupByDay'` block — keep exactly one closing `})` for it, as shown.)

- [ ] **Step 2: Run them to verify they fail**

Run: `sh -c "cd frontend && npx vitest run src/lib/board.test.ts"`
Expected: FAIL — `dateChips`/`filterByChip` are not exported; "Tomorrow" and the tab order assertions fail.

- [ ] **Step 3: Implement the helpers**

In `frontend/src/lib/board.ts`, replace `groupByDay` and `sportTabs` with:

```ts
const DAY_MS = 86_400_000

/** Whole days from `from` to `to` (both YYYY-MM-DD). */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS)
}

/** Days from `today` to the Monday that ends its betting week. Weeks run
 *  Tuesday-Monday ET, the NFL's week; Saturday college games fall inside it. */
function daysToMonday(today: string): number {
  return (8 - new Date(`${today}T00:00:00Z`).getUTCDay()) % 7
}

export function groupByDay(games: BoardGame[], today: string) {
  const days: { date: string; label: string; games: BoardGame[] }[] = []
  for (const g of games) {
    let day = days.find(d => d.date === g.date)
    if (!day) {
      const ahead = daysBetween(today, g.date)
      const label = ahead === 0 ? 'Today' : ahead === 1 ? 'Tomorrow' : formatDay(g.date)
      day = { date: g.date, label, games: [] }
      days.push(day)
    }
    day.games.push(g)
  }
  return days
}

/** The order a US sportsbook leads with; any other sport follows alphabetically. */
export const SPORT_ORDER = ['nfl', 'ncaaf', 'mlb', 'nba', 'nhl', 'ncaab', 'mma', 'boxing']

export function sportTabs(games: BoardGame[]): string[] {
  const rank = (s: string) => {
    const i = SPORT_ORDER.indexOf(s)
    return i === -1 ? SPORT_ORDER.length : i
  }
  return [...new Set(games.map(g => g.sport))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

export type DateChip = 'all' | 'today' | 'tomorrow' | 'this_week' | 'next_week'
export const DATE_CHIPS: Record<DateChip, string> = {
  all: 'All', today: 'Today', tomorrow: 'Tomorrow', this_week: 'This week', next_week: 'Next week',
}

function inChip(date: string, chip: DateChip, today: string): boolean {
  const ahead = daysBetween(today, date)
  const end = daysToMonday(today)
  switch (chip) {
    case 'all': return true
    case 'today': return ahead === 0
    case 'tomorrow': return ahead === 1
    case 'this_week': return ahead >= 0 && ahead <= end
    case 'next_week': return ahead > end && ahead <= end + 7
  }
}

export function filterByChip(games: BoardGame[], chip: DateChip, today: string): BoardGame[] {
  return games.filter(g => inChip(g.date, chip, today))
}

/** "All" plus each chip that would show at least one game, in display order. */
export function dateChips(games: BoardGame[], today: string): DateChip[] {
  return (Object.keys(DATE_CHIPS) as DateChip[])
    .filter(c => c === 'all' || games.some(g => inChip(g.date, c, today)))
}
```

- [ ] **Step 4: Run the helper tests**

Run: `sh -c "cd frontend && npx vitest run src/lib/board.test.ts"`
Expected: PASS.

- [ ] **Step 5: Write the failing Lobby tests**

In `frontend/src/pages/Lobby.test.tsx`, replace the test `'shows sport tabs in board order and the first sport by default'` with:

```tsx
  it('leads with NFL like a sportsbook and shows the first tab by default', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nba', '2026-10-20'), g(2, 'nfl', '2026-10-21')] })
    renderLobby()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map(t => t.textContent)).toEqual(['NFL', 'NBA'])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('A2')).toBeInTheDocument()
    expect(screen.queryByText('A1')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'NBA' }))
    expect(screen.getByText('A1')).toBeInTheDocument()
  })

  describe('looking ahead', () => {
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date('2026-10-09T16:00:00Z'))            // Friday noon ET
    })
    afterEach(() => vi.useRealTimers())

    it('filters a sport by date chip and resets the chip when the sport changes', async () => {
      vi.mocked(api.paper.board).mockResolvedValue({ games: [
        g(1, 'nfl', '2026-10-11'), g(2, 'nfl', '2026-10-18'), g(3, 'mlb', '2026-10-09')] })
      renderLobby()
      const when = await screen.findByRole('group', { name: 'When' })
      expect(Array.from(when.querySelectorAll('button')).map(b => b.textContent))
        .toEqual(['All', 'This week', 'Next week'])
      fireEvent.click(screen.getByRole('button', { name: 'Next week' }))
      expect(screen.getByText('A2')).toBeInTheDocument()
      expect(screen.queryByText('A1')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('tab', { name: 'MLB' }))
      fireEvent.click(screen.getByRole('tab', { name: 'NFL' }))
      expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByText('A1')).toBeInTheDocument()
    })

    it('heads each day with Today / Tomorrow / the date and its game count', async () => {
      vi.mocked(api.paper.board).mockResolvedValue({ games: [
        g(1, 'nfl', '2026-10-09'), g(2, 'nfl', '2026-10-10'), g(3, 'nfl', '2026-10-10'), g(4, 'nfl', '2026-10-18')] })
      renderLobby()
      expect(await screen.findByRole('heading', { name: 'Today · 1 game' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Tomorrow · 2 games' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Sun Oct 18 · 1 game' })).toBeInTheDocument()
    })
  })
```

and add `afterEach` to the vitest import at the top. In the test `'says so when the board is empty'` nothing changes (it matches `/No games on the board/`).

- [ ] **Step 6: Run them to verify they fail**

Run: `sh -c "cd frontend && npx vitest run src/pages/Lobby.test.tsx"`
Expected: FAIL — tab order is board order (`['NBA','NFL']`); no group named "When"; headings have no counts.

- [ ] **Step 7: Implement the Lobby**

Replace `frontend/src/pages/Lobby.tsx` with:

```tsx
import { useState } from 'react'
import BoardGameCard from '../components/BoardGameCard'
import ModelPicksStrip from '../components/ModelPicksStrip'
import { useBoard } from '../hooks/useBoard'
import { DATE_CHIPS, dateChips, etToday, filterByChip, groupByDay, sportTabs, type DateChip } from '../lib/board'
import { useSlip } from '../stores/slipStore'
import FeedTicker from '../components/FeedTicker'

export default function Lobby() {
  const board = useBoard()
  const games = board.data?.games ?? []
  // Any failed fetch locks the board, even with old data still on screen:
  // a price the server can't confirm must not be tappable.
  const offline = board.isError
  const tabs = sportTabs(games)
  const [chosen, setChosen] = useState<string | null>(null)
  const [chip, setChip] = useState<DateChip>('all')
  const sport = chosen && tabs.includes(chosen) ? chosen : tabs[0] ?? null
  const today = etToday(new Date())
  const ofSport = games.filter(g => g.sport === sport)
  const chips = dateChips(ofSport, today)
  const active = chips.includes(chip) ? chip : 'all'
  const shown = filterByChip(ofSport, active, today)
  const toggle = useSlip(s => s.toggle)

  return (
    <div className="sb-lobby">
      <FeedTicker />
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      {board.isLoading && <p className="sb-empty">Loading the board…</p>}
      {board.isSuccess && games.length === 0 &&
        <p className="sb-empty">No games on the board in the next 14 days.</p>}
      {tabs.length > 0 && (
        <div className="sb-sport-tabs" role="tablist" aria-label="Sports">
          {tabs.map(s => (
            <button key={s} role="tab" className="sb-sport-tab" aria-selected={s === sport}
              onClick={() => { setChosen(s); setChip('all') }}>{s.toUpperCase()}</button>
          ))}
        </div>
      )}
      {chips.length > 1 && (
        <div className="sb-filter sb-date-chips" role="group" aria-label="When">
          {chips.map(c => (
            <button key={c} type="button" aria-pressed={c === active} onClick={() => setChip(c)}>{DATE_CHIPS[c]}</button>
          ))}
        </div>
      )}
      <ModelPicksStrip games={shown} offline={offline} onPick={toggle} />
      {groupByDay(shown, today).map(day => (
        <section key={day.date} aria-label={day.label}>
          <h2 className="sb-day">{day.label} · {day.games.length} {day.games.length === 1 ? 'game' : 'games'}</h2>
          {day.games.map(game => <BoardGameCard key={game.id} game={game} offline={offline} onPick={toggle} />)}
        </section>
      ))}
    </div>
  )
}
```

In `frontend/src/api/client.ts` change `'/paper/board?days=7'` to `'/paper/board?days=14'`.

Append to `frontend/src/sportsbook.css`:

```css
.sb-date-chips { margin: 0.25rem 0 0.5rem; overflow-x: auto; flex-wrap: nowrap; }
.sb-date-chips button { white-space: nowrap; }
```

- [ ] **Step 8: Run the frontend suite**

Run: `sh -c "cd frontend && npx vitest run && npx tsc -b"`
Expected: all pass, tsc clean. Any other test asserting a day heading text without the count (grep `name: 'Today'` / `'Sun Oct` in `src/**/*.test.tsx`) is updated to the counted form — note each in the ledger.

- [ ] **Step 9: Mutation checks**

1. `daysToMonday`: `(8 - …) % 7` → `(7 - …) % 7` → the Monday/Sunday/Tuesday chip tests FAIL. Restore.
2. Lobby: drop `setChip('all')` from the tab onClick → "filters a sport by date chip and resets…" FAILS. Restore.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/lib/board.ts frontend/src/lib/board.test.ts frontend/src/pages/Lobby.tsx frontend/src/pages/Lobby.test.tsx frontend/src/api/client.ts frontend/src/sportsbook.css
git commit -m "feat(lobby): two weeks of games, date chips, counted day headers and sportsbook tab order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: No-information picks stay tracking-only (MMA stopgap)

**Files:**
- Modify: `backend/pipeline/pick_generator.py` (constant + helper near the top; one loop after the combat filter, before `keepers = …` ~line 287)
- Create: `backend/scripts/demote_no_information_picks.py`
- Create: `backend/tests/test_no_information_picks.py`

**Interfaces:**
- Produces: `NO_INFORMATION_PROB = 0.5`; `is_no_information(model_prob: float | None) -> bool` (in `pick_generator`); `candidates(session, now: datetime) -> list[PickModel]` and `main(argv) -> int` in the script.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_no_information_picks.py`:

```python
"""A pick at exactly the no-information 0.5 is tracked, not published.

Owner, 2026-10-09: 23 of 24 published MMA picks since 10-01 had model_prob
0.5 -- the model knew nothing about those fights, so every fighter priced
under even money read as a big "edge" and went out in the email. Until the
investigation (docs/FINDINGS.md) says why, such picks are stored as
tracking-only: kept for the record, never on the board or in the email.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import backend.pipeline.pick_generator as pg
from backend.data_types import Pick
from backend.models import Base, EloRating, Game, Odds, PickModel, StrategyModel, Team
from backend.scripts import demote_no_information_picks as demote

DAY = date(2026, 3, 1)


def _seed(session):
    session.add_all([Team(id=1, name="H", abbreviation="H1", sport="nba"),
                     Team(id=2, name="A", abbreviation="A1", sport="nba")])
    session.flush()
    session.add(Game(id=1, sport="nba", season="2025-26", date=DAY, home_team_id=1,
                     away_team_id=2, status="scheduled"))
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="nba", rating=1500),
        EloRating(team_id=2, sport="nba", rating=1500),
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="value_only", config_json="{}", is_active=True),
    ])
    session.commit()


def _fake_strategy(prob):
    class Fake:
        def __init__(self, name, config, sport_thresholds):
            pass

        def predict(self, game_data):
            return [Pick(game_id=1, pick_type="moneyline", pick_value="AWAY ML", confidence=3,
                         edge_pct=19.0, model_probability=prob, implied_probability=0.42,
                         odds_at_pick=130)]
    return Fake


@pytest.mark.parametrize("prob, tracked", [(0.5, True), (0.55, False), (0.4999, False)])
def test_a_pick_at_exactly_one_half_is_stored_as_tracking_only(db_engine, db_session, monkeypatch,
                                                                prob, tracked):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    monkeypatch.setitem(pg.STRATEGY_MAP, "value_only", _fake_strategy(prob))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    pick = db_session.query(PickModel).one()
    assert pick.tracking_only is tracked


def test_the_demote_script_moves_only_published_unstarted_one_half_picks(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    now = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)
    db_session.add_all([Team(id=1, name="H", abbreviation="H", sport="mma"),
                        Team(id=2, name="A", abbreviation="A", sport="mma"),
                        StrategyModel(id=1, name="x", config_json="{}")])
    db_session.flush()
    future = Game(id=1, sport="mma", season="2026", date=date(2026, 10, 10), status="scheduled",
                  home_team_id=1, away_team_id=2)
    started = Game(id=2, sport="mma", season="2026", date=date(2026, 10, 9), status="scheduled",
                   home_team_id=1, away_team_id=2, start_time=now - timedelta(hours=1))
    done = Game(id=3, sport="mma", season="2026", date=date(2026, 10, 3), status="final",
                home_team_id=1, away_team_id=2)
    db_session.add_all([future, started, done])
    db_session.flush()

    def pick(gid, prob, **kw):
        p = PickModel(game_id=gid, strategy_id=1, pick_type=kw.pop("pick_type", "moneyline"),
                      pick_value="AWAY ML", confidence=3, edge_pct=19.0, odds_at_pick=130,
                      model_prob=prob, **kw)
        db_session.add(p)
        return p

    target = pick(1, 0.5)
    pick(1, 0.61, pick_type="spread")             # has information
    pick(2, 0.5, pick_type="spread")              # game started
    pick(3, 0.5, pick_type="spread")              # graded history stays as emailed
    pick(1, 0.5, pick_type="over_under", tracking_only=True)   # already tracked
    db_session.commit()
    assert [p.id for p in demote.candidates(db_session, now)] == [target.id]
```

(`db_engine` / `db_session` are the repo's existing fixtures used by `test_pick_refresh.py`. `backend/scripts/` must be a package: if `backend/scripts/__init__.py` does not exist, create it empty and add it to the commit.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_no_information_picks.py -q`
Expected: FAIL — `ImportError` for `demote_no_information_picks` (collection error). After creating an empty script file temporarily is NOT the fix: write the generator change and the script in Step 3, then expect the 0.5 case to have failed before (verify by running only the parametrized test after Step 3a, see below).

- [ ] **Step 3a: Implement the generator rule; watch the parametrized test go red→green**

First create the script file with the content from Step 3b, so the module imports; run `pytest backend/tests/test_no_information_picks.py -q -k one_half` BEFORE editing `pick_generator.py`. Expected: the `0.5` case FAILS (`tracking_only` is False). Then in `backend/pipeline/pick_generator.py` add after the module's `STRATEGY_MAP` definition:

```python
#: A model probability of exactly one half means the model had no
#: information (equal ratings, no stats): any "edge" is the price alone.
#: Owner, 2026-10-09: such picks are tracked, not published, until the MMA
#: investigation (docs/FINDINGS.md) says why MMA had no information.
NO_INFORMATION_PROB = 0.5


def is_no_information(model_prob: float | None) -> bool:
    return model_prob is not None and abs(model_prob - NO_INFORMATION_PROB) < 1e-9
```

and immediately before the line `keepers = [p for p in picks if p.confidence >= 1` insert:

```python
            # A no-information pick is kept for the record but never
            # published (owner, 2026-10-09). Set before sizing, so it does not
            # shrink the stake of the picks that are bets; _refresh_pick copies
            # the flag, so a refreshed pick follows the same rule.
            for p in picks:
                if is_no_information(getattr(p, "model_probability", None)):
                    p.tracking_only = True
```

Run `pytest backend/tests/test_no_information_picks.py -q -k one_half` → PASS (3).

- [ ] **Step 3b: The one-off script**

`backend/scripts/demote_no_information_picks.py`:

```python
"""One-off (owner, 2026-10-09): move published no-information picks on games
not yet started to tracking-only.

The pick generator now stores a pick at exactly model_prob 0.5 as
tracking-only (pick_generator.is_no_information); this applies the same rule
to the picks already published for games still to play, so they leave the
board and the email at once instead of at their next window refresh. Graded
history is left exactly as it was emailed.

    python -m backend.scripts.demote_no_information_picks            # dry run
    python -m backend.scripts.demote_no_information_picks --apply    # one transaction
"""
import sys
from datetime import datetime, timezone

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, PickModel
from backend.paper.pricing import open_for_betting
from backend.pipeline.pick_generator import NO_INFORMATION_PROB, is_no_information
from backend.pipeline.pick_versions import record_pick_version


def candidates(session, now: datetime) -> list[PickModel]:
    rows = (session.query(PickModel, Game).join(Game, Game.id == PickModel.game_id)
            .filter(PickModel.published(), PickModel.by_model(),
                    PickModel.pick_type != "prop",
                    PickModel.model_prob == NO_INFORMATION_PROB)
            .order_by(PickModel.id).all())
    return [p for p, g in rows if is_no_information(p.model_prob) and open_for_betting(g, now)]


def main(argv: list[str]) -> int:
    config = load_config("config.yaml")
    session = get_session(get_engine(config["database_path"]))
    try:
        picks = candidates(session, datetime.now(timezone.utc))
        for p in picks:
            print(f"pick {p.id} game {p.game_id} {p.pick_type} {p.pick_value} "
                  f"edge {p.edge_pct} model_prob {p.model_prob}")
        print(f"{len(picks)} pick(s) to move to tracking-only")
        if "--apply" in argv and picks:
            for p in picks:
                p.tracking_only = True
                record_pick_version(session, p, "no_information")
            session.commit()
            print("applied")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the file and the generator tests**

Run: `.venv/Scripts/python -m pytest backend/tests/test_no_information_picks.py backend/tests/test_pick_refresh.py backend/tests/test_pick_generator.py backend/tests/test_pick_generator_versions.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation checks**

1. Delete the `for p in picks: if is_no_information…` loop → the `0.5` case FAILS. Restore.
2. In `candidates`, drop `and open_for_betting(g, now)` → the demote test FAILS (returns the started/final picks too). Restore.
Delete `__pycache__` for both modules after each write and restore.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/pick_generator.py backend/scripts/demote_no_information_picks.py backend/tests/test_no_information_picks.py
git commit -m "fix(picks): a pick at the no-information 0.5 is tracked, not published (MMA stopgap)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Add `backend/scripts/__init__.py` to the `git add` if Step 1 created it.)

---

### Task 4: The model explains its pick (`rationale.pick_note`, board `reasoning`)

**Files:**
- Modify: `backend/analysis/odds_utils.py` (add `prob_to_american`)
- Modify: `backend/analysis/rationale.py` (add `factors_from_json`, `SPORT_CAVEATS`, `DEFAULT_CAVEAT`, `pick_note`)
- Modify: `backend/digest/selector.py` (`_rationale_for` uses `factors_from_json`)
- Modify: `backend/paper/board.py` (`_model_picks` returns rows; `_model_pick_view`)
- Modify: `backend/tests/test_rationale.py`, `backend/tests/test_paper_board.py`

**Interfaces:**
- Produces: `prob_to_american(prob: float) -> int` (raises `ValueError` outside (0, 1)); `factors_from_json(text: str | None) -> list[PickFactor]`; `pick_note(*, sport, pick_type, pick_value, home, away, model_prob, market_prob, edge_pct, odds, factors) -> str`. Board `model_pick` = `{pick_type, pick_value, odds, edge_pct, reasoning}` where `reasoning` is `None` (no `model_prob`) or `{model_prob, market_prob, edge_pct, fair_odds, units, note}`.

- [ ] **Step 1: Write the failing rationale tests**

Append to `backend/tests/test_rationale.py`:

```python
import pytest

from backend.analysis.odds_utils import prob_to_american
from backend.analysis.rationale import DEFAULT_CAVEAT, factors_from_json, pick_note


def test_prob_to_american_is_the_margin_free_price():
    assert prob_to_american(0.58) == -138
    assert prob_to_american(0.4) == 150
    assert prob_to_american(0.5) == -100
    with pytest.raises(ValueError):
        prob_to_american(1.0)


def test_factors_from_json_reads_codes_and_ignores_junk():
    fs = factors_from_json('[{"code": "rating_gap", "side": "home", "strength": "slight"}, 7]')
    assert [(f.code, f.side, f.strength) for f in fs] == [("rating_gap", "home", "slight")]
    assert factors_from_json(None) == [] and factors_from_json("not json") == []
    assert factors_from_json('{"a": 1}') == []


def _note(**kw):
    base = dict(sport="nfl", pick_type="moneyline", pick_value="HOME ML", home="Kansas City",
                away="Buffalo", model_prob=0.58, market_prob=0.52, edge_pct=10.7, odds=-110,
                factors=factors_from_json('[{"code": "rating_gap", "side": "home", "strength": "slight"}]'))
    return pick_note(**{**base, **kw})


def test_a_full_nfl_moneyline_note():
    assert _note() == (
        "The model gives Kansas City a 58% chance to win; the books' price, with their margin "
        "removed, says 52%. At -110 that is a 10.7% edge (fair price -138). Rating gap slightly "
        "favors Kansas City. The model has not shown an edge over NFL closing lines yet, so treat "
        "this as one opinion, not a sure thing.")


def test_spread_and_total_wording():
    assert _note(pick_type="spread", pick_value="AWAY +3.5", factors=[]).startswith(
        "The model gives Buffalo +3.5 a 58% chance to cover;")
    assert _note(pick_type="over_under", pick_value="Over 47.5", factors=[]).startswith(
        "The model gives the Over 47.5 a 58% chance to hit;")


def test_missing_numbers_are_left_out_never_invented():   # Review Focus 4
    note = _note(market_prob=None, odds=None, factors=[], sport="ncaaf")
    assert note == ("The model gives Kansas City a 58% chance to win. " + DEFAULT_CAVEAT)
    assert "None" not in note and "nan" not in note.lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_rationale.py -q`
Expected: FAIL — `ImportError: cannot import name 'prob_to_american'`.

- [ ] **Step 3: Implement**

Append to `backend/analysis/odds_utils.py`:

```python
def prob_to_american(prob: float) -> int:
    """American odds whose implied probability is ``prob``, with no margin
    (a fair price): 0.58 -> -138, 0.40 -> +150, 0.50 -> -100."""
    if not 0 < prob < 1:
        raise ValueError(f"probability must be strictly between 0 and 1, got {prob}")
    if prob >= 0.5:
        return -round(100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)
```

In `backend/analysis/rationale.py` add `import json` at the top, and append:

```python
def factors_from_json(text: str | None) -> list[PickFactor]:
    """A pick's stored `rationale_json` as factors; anything malformed is []."""
    if not text:
        return []
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [PickFactor(code=f.get("code", ""), side=f.get("side", "home"),
                       strength=f.get("strength", "moderate"))
            for f in raw if isinstance(f, dict)]


#: The sentence that closes every model-pick note, worded ONLY from measured
#: results. NFL/MLB: shrink weight 0.00, no edge over the closing line shown
#: (memory: shrunk edge / CLV report). Other sports: nothing measured to say.
SPORT_CAVEATS = {
    "nfl": ("The model has not shown an edge over NFL closing lines yet, so treat "
            "this as one opinion, not a sure thing."),
    "mlb": ("The model has not shown an edge over MLB closing lines yet, so treat "
            "this as one opinion, not a sure thing."),
}
DEFAULT_CAVEAT = "This is the model's opinion, not a sure thing; its record is on the Track Record page."

_OUTCOME = {"moneyline": "to win", "spread": "to cover", "over_under": "to hit"}


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _odds(o: int) -> str:
    return f"+{o}" if o > 0 else str(o)


def _who(pick_type: str, pick_value: str, home: str, away: str) -> str:
    side, _, rest = pick_value.partition(" ")
    if pick_type == "over_under":
        return f"the {pick_value}"
    team = home if side == "HOME" else away
    return team if pick_type == "moneyline" else f"{team} {rest}"


def pick_note(*, sport: str, pick_type: str, pick_value: str, home: str, away: str,
              model_prob: float, market_prob: float | None, edge_pct: float | None,
              odds: int | None, factors: list[PickFactor]) -> str:
    """Why the model made a game pick, in plain words, from its own numbers:
    its chance vs the market's margin-free chance, the edge it was priced at,
    the factors it actually computed, and the per-sport caveat. A missing
    number drops its clause; nothing is ever filled in."""
    from backend.analysis.odds_utils import prob_to_american
    first = (f"The model gives {_who(pick_type, pick_value, home, away)} a "
             f"{_pct(model_prob)} chance {_OUTCOME.get(pick_type, 'to win')}")
    if market_prob is not None:
        first += f"; the books' price, with their margin removed, says {_pct(market_prob)}"
    parts = [first + "."]
    if odds is not None and edge_pct is not None:
        price = f"At {_odds(odds)} that is a {edge_pct:.1f}% edge"
        if 0 < model_prob < 1:
            price += f" (fair price {_odds(prob_to_american(model_prob))})"
        parts.append(price + ".")
    because = render_rationale(factors, home, away)
    if because:
        parts.append(because)
    parts.append(SPORT_CAVEATS.get(sport, DEFAULT_CAVEAT))
    return " ".join(parts)
```

In `backend/digest/selector.py`, replace the body of `_rationale_for` from `if not pick.rationale_json:` through the `]` closing the `factors = [...]` list with:

```python
    factors = factors_from_json(pick.rationale_json)
    if not factors:
        return ""
```

and import it: `from backend.analysis.rationale import factors_from_json, render_rationale` (adjust the existing import of `render_rationale`). Remove `PickFactor`/`json` imports there only if nothing else in the file uses them (`grep -n "PickFactor\|json\." backend/digest/selector.py`).

- [ ] **Step 4: Run them**

Run: `.venv/Scripts/python -m pytest backend/tests/test_rationale.py backend/tests -q -k "rationale or digest or selector"`
Expected: PASS.

- [ ] **Step 5: Write the failing board test**

In `backend/tests/test_paper_board.py`, in `test_model_pick_is_the_highest_edge_published_model_game_pick`, replace the final two asserts' first one with:

```python
    view = by_id[gid]["model_pick"]
    assert {k: v for k, v in view.items() if k != "reasoning"} == {
        "pick_type": "over_under", "pick_value": "Over 47.5", "odds": -110, "edge_pct": 4.0}
    assert view["reasoning"] is None          # no model_prob stored: nothing to explain
```

and append a new test:

```python
def test_model_pick_carries_its_reasoning():
    client = _client()
    gid = _game(client)
    s = get_session(client.app.state.engine)
    s.add(Strategy(id=1, name="ensemble", config_json="{}"))
    s.flush()
    s.add(PickModel(game_id=gid, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                    confidence=3, edge_pct=10.7, odds_at_pick=-110, model_prob=0.58,
                    market_prob_novig=0.52, suggested_unit_size=0.87,
                    rationale_json='[{"code": "rating_gap", "side": "home", "strength": "slight"}]'))
    s.commit()
    s.close()
    game = next(g for g in client.get("/paper/board").json()["games"] if g["id"] == gid)
    r = game["model_pick"]["reasoning"]
    home = game["home_team"]
    assert {k: r[k] for k in ("model_prob", "market_prob", "edge_pct", "fair_odds", "units")} == {
        "model_prob": 0.58, "market_prob": 0.52, "edge_pct": 10.7, "fair_odds": -138, "units": 0.87}
    assert r["note"] == (
        f"The model gives {home} a 58% chance to win; the books' price, with their margin removed, "
        f"says 52%. At -110 that is a 10.7% edge (fair price -138). Rating gap slightly favors "
        f"{home}. The model has not shown an edge over NFL closing lines yet, so treat this as one "
        f"opinion, not a sure thing.")
```

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_board.py -q`
Expected: FAIL — `KeyError: 'reasoning'`.

- [ ] **Step 6: Implement the board view**

In `backend/paper/board.py`: add imports

```python
from backend.analysis.odds_utils import prob_to_american
from backend.analysis.rationale import factors_from_json, pick_note
```

change `_model_picks` to keep the row (its docstring stays):

```python
    best: dict[int, PickModel] = {}
    for p in rows:
        best.setdefault(p.game_id, p)
    return best
```

(and its return annotation to `dict[int, PickModel]`), add:

```python
def _model_pick_view(p: PickModel | None, sport: str, home: str, away: str) -> dict | None:
    if p is None:
        return None
    return {"pick_type": p.pick_type, "pick_value": p.pick_value,
            "odds": p.odds_at_pick, "edge_pct": p.edge_pct,
            "reasoning": _reasoning(p, sport, home, away)}


def _reasoning(p: PickModel, sport: str, home: str, away: str) -> dict | None:
    """Why the model made this pick: its own numbers and the note
    `rationale.pick_note` writes from them. None without a probability."""
    if p.model_prob is None:
        return None
    return {
        "model_prob": round(p.model_prob, 4),
        "market_prob": None if p.market_prob_novig is None else round(p.market_prob_novig, 4),
        "edge_pct": p.edge_pct,
        "fair_odds": prob_to_american(p.model_prob) if 0 < p.model_prob < 1 else None,
        "units": p.suggested_unit_size,
        "note": pick_note(sport=sport, pick_type=p.pick_type, pick_value=p.pick_value,
                          home=home, away=away, model_prob=p.model_prob,
                          market_prob=p.market_prob_novig, edge_pct=p.edge_pct,
                          odds=p.odds_at_pick, factors=factors_from_json(p.rationale_json)),
    }
```

and in `build_board` replace `"model_pick": picks.get(game.id),` with
`"model_pick": _model_pick_view(picks.get(game.id), game.sport, home.abbreviation, away.abbreviation),`.

- [ ] **Step 7: Run the board tests and the full backend suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (record the count in the ledger).

- [ ] **Step 8: Mutation checks**

1. In `pick_note`, drop the `if market_prob is not None:` guard (always append) → `test_missing_numbers_are_left_out_never_invented` FAILS. Restore.
2. In `SPORT_CAVEATS`, delete the `"nfl"` entry → `test_a_full_nfl_moneyline_note` and `test_model_pick_carries_its_reasoning` FAIL. Restore.
Delete `backend/analysis/__pycache__` after each write and restore.

- [ ] **Step 9: Commit**

```bash
git add backend/analysis/odds_utils.py backend/analysis/rationale.py backend/digest/selector.py backend/paper/board.py backend/tests/test_rationale.py backend/tests/test_paper_board.py
git commit -m "feat(picks): every model pick on the board explains itself from its own numbers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: "Why this pick" in the Lobby strip and on the game page

**Files:**
- Modify: `frontend/src/types.ts` (`PickReasoning`; `ModelPick.reasoning`)
- Create: `frontend/src/components/PickReasoning.tsx`, `frontend/src/components/PickReasoning.test.tsx`
- Modify: `frontend/src/components/ModelPicksStrip.tsx`
- Modify: `frontend/src/pages/GameDetail.tsx`, `frontend/src/pages/GameDetail.test.tsx`
- Modify: `frontend/src/sportsbook.css`

**Interfaces:**
- Consumes: board `model_pick.reasoning` from Task 4.
- Produces: `export interface PickReasoning { model_prob: number; market_prob: number | null; edge_pct: number; fair_odds: number | null; units: number | null; note: string }`; `ModelPick.reasoning?: PickReasoning | null`; `export default function PickReasoningPanel({ r }: { r: PickReasoning })`.

- [ ] **Step 1: Types**

In `frontend/src/types.ts`, before `export interface ModelPick`, add:

```ts
/** Why the model made a pick (board `model_pick.reasoning`): its numbers and a note written from them. */
export interface PickReasoning {
  model_prob: number; market_prob: number | null; edge_pct: number;
  fair_odds: number | null; units: number | null; note: string;
}
```

and change `ModelPick` to:

```ts
export interface ModelPick {
  pick_type: GamePickType; pick_value: string; odds: number | null; edge_pct: number;
  reasoning?: PickReasoning | null;
}
```

- [ ] **Step 2: Write the failing tests**

`frontend/src/components/PickReasoning.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PickReasoningPanel from './PickReasoning'
import ModelPicksStrip from './ModelPicksStrip'
import type { BoardGame, GameQuote, PickReasoning } from '../types'

const r: PickReasoning = { model_prob: 0.58, market_prob: 0.52, edge_pct: 10.7, fair_odds: -138, units: 0.87,
  note: 'The model gives Bucs a 58% chance to win.' }

describe('PickReasoningPanel', () => {
  it("shows the model's numbers and its note", () => {
    render(<PickReasoningPanel r={r} />)
    const panel = screen.getByLabelText('Why this pick')
    for (const text of ['58%', '52%', '10.7%', '-138', '0.87u', 'The model gives Bucs a 58% chance to win.'])
      expect(panel).toHaveTextContent(text)
  })
  it('leaves out what it does not have, never NaN or null (Review Focus 4)', () => {
    render(<PickReasoningPanel r={{ ...r, market_prob: null, fair_odds: null, units: null }} />)
    const panel = screen.getByLabelText('Why this pick')
    expect(panel).not.toHaveTextContent('Market')
    expect(panel).not.toHaveTextContent('Fair price')
    expect(panel).not.toHaveTextContent(/NaN|null|undefined/)
  })
})

describe('ModelPicksStrip', () => {
  const ml = (side: string, odds: number): GameQuote => ({ pick_type: 'moneyline', side, available: true, odds,
    line: null, pick_value: `${side} ML`, quoted_at: 'x', prop_player: null, prop_market: null } as GameQuote)
  const g: BoardGame = { id: 1, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Bucs',
    away_team: 'Cowboys', prop_count: 0, quotes: [ml('HOME', -150), ml('AWAY', 130)],
    model_pick: { pick_type: 'moneyline', pick_value: 'HOME ML', odds: -150, edge_pct: 10.7, reasoning: r } }

  it('opens the reasoning from "Why?"', () => {
    render(<ModelPicksStrip games={[g]} offline={false} onPick={vi.fn()} />)
    expect(screen.queryByLabelText('Why this pick')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.getByLabelText('Why this pick')).toHaveTextContent('58%')
    expect(screen.getByRole('button', { name: 'Why?' })).toHaveAttribute('aria-expanded', 'true')
  })
  it('has no "Why?" for a pick without reasoning', () => {
    render(<ModelPicksStrip games={[{ ...g, model_pick: { ...g.model_pick!, reasoning: null } }]} offline={false} onPick={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Why?' })).toBeNull()
  })
})
```

In `frontend/src/pages/GameDetail.test.tsx`, inside `describe('GameDetail', …)`, add:

```tsx
  it("explains the model's pick on the game page", async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [{ ...game, model_pick: {
      pick_type: 'moneyline', pick_value: 'HOME ML', odds: -150, edge_pct: 10.7, reasoning: {
        model_prob: 0.58, market_prob: 0.52, edge_pct: 10.7, fair_odds: -138, units: 0.87,
        note: 'The model gives Bucs a 58% chance to win.' } } }] })
    renderAt('/game/5')
    expect(await screen.findByRole('heading', { name: 'Why the model likes Bucs ML' })).toBeInTheDocument()
    expect(screen.getByLabelText('Why this pick')).toHaveTextContent('The model gives Bucs a 58% chance to win.')
  })
```

- [ ] **Step 3: Run them to verify they fail**

Run: `sh -c "cd frontend && npx vitest run src/components/PickReasoning.test.tsx src/pages/GameDetail.test.tsx"`
Expected: FAIL — `./PickReasoning` cannot be resolved; GameDetail has no such heading.

- [ ] **Step 4: Implement**

`frontend/src/components/PickReasoning.tsx`:

```tsx
import { formatOdds } from '../lib/quotes'
import type { PickReasoning } from '../types'

const pct = (p: number) => `${Math.round(p * 100)}%`

/** The model's case for a pick: its chance vs the market's, the edge, the fair
 *  price, the stake it would make, and the note `rationale.pick_note` wrote. */
export default function PickReasoningPanel({ r }: { r: PickReasoning }) {
  return (
    <div className="sb-why" aria-label="Why this pick">
      <dl className="sb-why-nums">
        <div><dt>Model</dt><dd>{pct(r.model_prob)}</dd></div>
        {r.market_prob !== null && <div><dt>Market</dt><dd>{pct(r.market_prob)}</dd></div>}
        <div><dt>Edge</dt><dd>{r.edge_pct.toFixed(1)}%</dd></div>
        {r.fair_odds !== null && <div><dt>Fair price</dt><dd>{formatOdds(r.fair_odds)}</dd></div>}
        {r.units !== null && <div><dt>Model stake</dt><dd>{r.units.toFixed(2)}u</dd></div>}
      </dl>
      <p className="sb-why-note">{r.note}</p>
    </div>
  )
}
```

In `frontend/src/components/ModelPicksStrip.tsx`: add `import { useState } from 'react'` and `import PickReasoningPanel from './PickReasoning'`; inside `ModelPicksStrip` add `const [open, setOpen] = useState<number | null>(null)` before the `return`, and replace the card body

```tsx
          <div className="sb-strip-card" key={g.id}>
            {/* The edge was computed at the model's price, which the live tile may no longer show. */}
            <small>MODEL PICK · Model edge {mp.edge_pct.toFixed(1)}%{mp.odds !== null && ` at ${formatOdds(mp.odds)}`}</small>
            <strong>{label}</strong>
            <StripTile g={g} q={q} label={label} offline={offline} onPick={onPick} />
          </div>
```

with

```tsx
          <div className="sb-strip-card" key={g.id}>
            {/* The edge was computed at the model's price, which the live tile may no longer show. */}
            <small>MODEL PICK · Model edge {mp.edge_pct.toFixed(1)}%{mp.odds !== null && ` at ${formatOdds(mp.odds)}`}</small>
            <strong>{label}</strong>
            <StripTile g={g} q={q} label={label} offline={offline} onPick={onPick} />
            {mp.reasoning && (
              <button type="button" className="sb-why-toggle" aria-expanded={open === g.id}
                onClick={() => setOpen(open === g.id ? null : g.id)}>Why?</button>
            )}
            {mp.reasoning && open === g.id && <PickReasoningPanel r={mp.reasoning} />}
          </div>
```

In `frontend/src/pages/GameDetail.tsx`: add `import PickReasoningPanel from '../components/PickReasoning'`, add `resolveLabel` to the existing `../lib/quotes` import (or add `import { resolveLabel } from '../lib/quotes'` if none), and after the line `<p className="sb-card-head" style={{ padding: 0 }}>{startLabel(game.start_time)}</p>` insert:

```tsx
      {game.model_pick?.reasoning && (
        <section className="sb-card sb-why-card">
          <h2>Why the model likes {resolveLabel(game.model_pick.pick_value, game.home_team, game.away_team)}</h2>
          <PickReasoningPanel r={game.model_pick.reasoning} />
        </section>
      )}
```

Append to `frontend/src/sportsbook.css`:

```css
.sb-why { margin-top: 0.5rem; padding: 0.6rem 0.7rem; border-radius: var(--radius-md); background: var(--bg-surface); border: 1px solid var(--border-default); }
.sb-why-nums { display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; margin: 0 0 0.4rem; }
.sb-why-nums dt { color: var(--text-muted); font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.sb-why-nums dd { margin: 0; font-weight: 800; font-variant-numeric: tabular-nums; }
.sb-why-note { margin: 0; font-size: 0.8rem; line-height: 1.4; color: var(--text-secondary); }
.sb-why-toggle { margin-top: 0.35rem; background: none; border: 0; padding: 0; color: var(--accent); font-weight: 700; cursor: pointer; }
.sb-why-card h2 { font-size: 0.85rem; margin: 0 0 0.25rem; }
```

- [ ] **Step 5: Run the frontend suite**

Run: `sh -c "cd frontend && npx vitest run && npx tsc -b"`
Expected: all pass, tsc clean.

- [ ] **Step 6: Mutation check**

In `PickReasoning.tsx`, render `<div><dt>Market</dt><dd>{pct(r.market_prob ?? NaN)}</dd></div>` unconditionally → "leaves out what it does not have" FAILS. Restore.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/PickReasoning.tsx frontend/src/components/PickReasoning.test.tsx frontend/src/components/ModelPicksStrip.tsx frontend/src/pages/GameDetail.tsx frontend/src/pages/GameDetail.test.tsx frontend/src/sportsbook.css
git commit -m "feat(picks): a Why? panel on the model's picks in the Lobby and on the game page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: A placed bet can carry a note (backend)

**Files:**
- Modify: `backend/models.py` (`PaperPick.note`)
- Modify: `backend/database.py` (`migrate_paper_pick_note`, appended to `MIGRATIONS`)
- Modify: `backend/api/users.py` (`NOTE_MAX`, `note` on `GameBetRequest`/`PropBetRequest`, `_feed_leg` exclude, `place_pick` stores and announces it)
- Modify: `backend/paper/bets.py` (`tickets` → `"note"`)
- Modify: `docs/data-dictionary.md` (paper bets section: `note`)
- Create: `backend/tests/test_bet_notes.py`

**Interfaces:**
- Produces: `paper_picks.note TEXT NULL`; request field `note: str | None` (≤ `NOTE_MAX` = 1000, stripped, blank → None) on straight bets only; ticket `"note": str | None`; placed-bet feed payload `"note"` (only when set). Feed `legs` never contain `note`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_bet_notes.py`:

```python
"""A straight bet can carry the bettor's written reasoning (spec B2).

Claude's NFL picks are written with reasoning at pick time; the note puts it
next to the bet on the ticket and in the league feed. The note is the
bettor's own: Tail copies the bet, never the note.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from backend.api.main import create_app
from backend.database import migrate_paper_pick_note
from backend.tests.auth_helpers import ALL_HEADERS
from backend.tests.test_api_users import _make_user, _seed_games


def _setup():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    [gid] = _seed_games(client, [{"status": "scheduled"}])
    return client, gid, _make_user(client)


def _bet(client, uid, gid, **extra):
    return client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100, **extra})


def test_a_note_is_on_the_ticket_and_in_the_feed_but_not_in_the_tail_legs():   # Review Focus 3
    client, gid, uid = _setup()
    note = "Backup QB starts; the line has not moved. Confidence 2/5 · Model: no side."
    assert _bet(client, uid, gid, note=note).status_code == 200
    [ticket] = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert ticket["note"] == note
    [event] = [e for e in client.get("/users/feed").json() if e["event_type"] == "pick_placed"]
    assert event["payload"]["note"] == note
    assert all("note" not in leg for leg in event["payload"]["legs"])


def test_markup_is_stored_as_text_and_the_limit_is_1000(): # Review Focus 2
    client, gid, uid = _setup()
    assert _bet(client, uid, gid, note="<b>bold</b>" + "x" * 989).status_code == 200
    [ticket] = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert ticket["note"].startswith("<b>bold</b>") and len(ticket["note"]) == 1000
    assert _bet(client, uid, gid, note="x" * 1001).status_code == 422


def test_no_note_or_a_blank_one_stores_nothing():
    client, gid, uid = _setup()
    assert _bet(client, uid, gid).status_code == 200
    assert _bet(client, uid, gid, note="   ").status_code == 200
    tickets = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert [t["note"] for t in tickets] == [None, None]
    events = [e for e in client.get("/users/feed").json() if e["event_type"] == "pick_placed"]
    assert all("note" not in e["payload"] for e in events)


def test_the_migration_adds_the_column_once():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE paper_picks (id INTEGER PRIMARY KEY, stake FLOAT)"))
    migrate_paper_pick_note(engine)
    migrate_paper_pick_note(engine)                      # idempotent
    assert "note" in [c["name"] for c in inspect(engine).get_columns("paper_picks")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_bet_notes.py -q`
Expected: FAIL — `ImportError: cannot import name 'migrate_paper_pick_note'`.

- [ ] **Step 3: Implement**

`backend/models.py`, in `class PaperPick`, after `graded_at = …`:

```python
    #: The bettor's written reasoning, shown on the ticket and in the feed
    #: (Claude's picks since 2026-10-09). Straight bets only; never copied by Tail.
    note = Column(Text, nullable=True)
```

`backend/database.py`, after `migrate_pick_tracking_only`:

```python
def migrate_paper_pick_note(engine):
    """Add paper_picks.note if missing (2026-10-09): the bettor's written
    reasoning for a straight bet. NULL for every earlier bet -- none had one."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "paper_picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("paper_picks")]
        if "note" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE paper_picks ADD COLUMN note TEXT"))
```

and add `migrate_paper_pick_note,` after `migrate_game_live_detail,` in the `MIGRATIONS` tuple.

`backend/api/users.py`:
- after the `_Strict` class add:

```python
#: A bet note's limit: Claude's longest reasoning so far was 654 characters.
NOTE_MAX = 1000


def _clean_note(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None
```

- replace `GameBetRequest` and `PropBetRequest` with:

```python
class GameBetRequest(GameLeg):
    stake: float = Field(allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @field_validator("note")
    @classmethod
    def _note(cls, v):
        return _clean_note(v)


class PropBetRequest(PropLeg):
    stake: float = Field(allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @field_validator("note")
    @classmethod
    def _note(cls, v):
        return _clean_note(v)
```

- in `_feed_leg`, change `exclude={"stake", "expected_odds", "expected_line"}` to `exclude={"stake", "expected_odds", "expected_line", "note"}` and add to its docstring: "The note is the bettor's, not the bet's: a Tail never carries it."
- in `place_pick`, add `note=bet.note,` to the `PaperPick(...)` constructor; build the feed payload as a dict and add the note only when set:

```python
        payload = {
            "user_name": user_name,
            "message": f"{user_name} bet {placed_leg['label']} {odds_str} — ${bet.stake:,.0f}",
            "pick_value": quote.pick_value,
            "odds": quote.odds,
            "stake": bet.stake,
            "bet_id": pick.id,
            "kind": "straight",
            "legs": [placed_leg],
        }
        if bet.note:
            payload["note"] = bet.note
        _log_feed_event(session, loop, user_id, "pick_placed", payload)
```

`backend/paper/bets.py`, in `tickets`: add `"note": pick.note,` to the straight ticket dict and `"note": None,` to the parlay ticket dict.

`docs/data-dictionary.md`, in the section "## Paper bets (`paper_picks`, `parlays`) -- not exported", add a bullet:

```
- `paper_picks.note` (2026-10-09): the bettor's own written reasoning for a straight bet (up to
  1000 characters), shown on the ticket and in the feed. NULL for every bet before 2026-10-09 and
  for any bet placed without one. Used by Claude's NFL picks; the two Claude slates before it were
  backfilled from logs-archive. Not model output.
```

- [ ] **Step 4: Run them and the suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Mutation checks**

1. `_feed_leg` exclude without `"note"` → the Review Focus 3 test FAILS. Restore.
2. `_clean_note` returning `v` unstripped → the blank-note test FAILS. Restore.
Delete `backend/api/__pycache__` after each write and restore.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/database.py backend/api/users.py backend/paper/bets.py docs/data-dictionary.md backend/tests/test_bet_notes.py
git commit -m "feat(bets): a straight bet can carry the bettor's note, on the ticket and in the feed, never tailed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Notes on tickets and in the feed (frontend)

**Files:**
- Modify: `frontend/src/types.ts` (`Ticket.note?`, `FeedPayload.note?`)
- Modify: `frontend/src/components/TicketCard.tsx`, `frontend/src/components/FeedList.tsx`
- Modify: `frontend/src/components/TicketList.test.tsx`, `frontend/src/pages/Leaders.test.tsx`
- Modify: `frontend/src/sportsbook.css`

- [ ] **Step 1: Write the failing tests**

Append inside `describe('TicketList', …)` in `frontend/src/components/TicketList.test.tsx`:

```tsx
  it("shows the bettor's note as plain text (Review Focus 2)", () => {
    renderList({ tickets: [t({ id: 7, note: '<b>Backup QB</b> starts. Confidence 2/5 · Model: no side.' })], emptyOpen: 'none' })
    const card = screen.getByRole('article', { name: 'Bet #P-7' })
    expect(card).toHaveTextContent('<b>Backup QB</b> starts. Confidence 2/5 · Model: no side.')
    expect(card.querySelector('b')).toBeNull()
  })
```

Append inside `describe('Leaders', …)` in `frontend/src/pages/Leaders.test.tsx`:

```tsx
  it("shows a placed bet's note under it in the feed", async () => {
    vi.mocked(api.users.feed).mockResolvedValue([{ ...placed, payload: { ...placed.payload, note: 'Line has not moved.' } }])
    renderPage()
    expect(await screen.findByText('Line has not moved.')).toBeInTheDocument()
  })
```

Run: `sh -c "cd frontend && npx vitest run src/components/TicketList.test.tsx src/pages/Leaders.test.tsx"`
Expected: FAIL — tsc-level: `note` is not on `Ticket` (vitest still runs; the assertions fail: text not found).

- [ ] **Step 2: Implement**

`frontend/src/types.ts`: in `export interface Ticket { … }` add `note?: string | null;` and in `FeedPayload` add `note?: string;`.

`frontend/src/components/TicketCard.tsx`: immediately before `<footer className="sb-bet-foot">` insert

```tsx
      {t.note && <p className="sb-bet-note">{t.note}</p>}
```

`frontend/src/components/FeedList.tsx`: after `<span className="sb-feed-msg">{e.payload?.message ?? ''}</span>` insert

```tsx
            {e.payload?.note && <p className="sb-feed-note">{e.payload.note}</p>}
```

Append to `frontend/src/sportsbook.css`:

```css
.sb-bet-note, .sb-feed-note { margin: 0.35rem 0 0; font-size: 0.78rem; line-height: 1.4; color: var(--text-secondary); white-space: pre-line; }
.sb-feed-note { flex-basis: 100%; }
```

- [ ] **Step 3: Run the suite**

Run: `sh -c "cd frontend && npx vitest run && npx tsc -b"`
Expected: all pass, tsc clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/TicketCard.tsx frontend/src/components/FeedList.tsx frontend/src/components/TicketList.test.tsx frontend/src/pages/Leaders.test.tsx frontend/src/sportsbook.css
git commit -m "feat(bets): show a bet's note on its ticket and under it in the league feed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Claude's picks send their reasoning; the two earlier slates are backfilled

**Files:**
- Create: `backend/scripts/backfill_claude_notes.py`
- Create: `backend/tests/test_backfill_claude_notes.py`
- Modify: `logs-archive/claude-picks-procedure.md` (step 5) — only if `git ls-files logs-archive/claude-picks-procedure.md` lists it; otherwise edit it in place uncommitted and say so in the ledger.

**Interfaces:**
- Produces: `note_for(pick: dict) -> str`; `match_bets(session, picks: list[dict], day: date) -> list[tuple[PaperPick, str]]`; `main(argv) -> int`. CLAUDE_USER_ID = 3.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_backfill_claude_notes.py`:

```python
"""Claude's reasoning from logs-archive/claude-picks-*.json onto its bets."""
from datetime import date

from backend.models import Base, Game, PaperPick, Team, UserProfile
from backend.scripts.backfill_claude_notes import match_bets, note_for

PICK = {"game": "Buccaneers @ Cowboys", "pick": "Buccaneers +9", "confidence": "2/5",
        "why": "Mayfield is out.", "vs_model": "no side"}


def test_note_for_joins_the_reasoning_confidence_and_model_view():
    assert note_for(PICK) == "Mayfield is out. Confidence 2/5 · Model: no side."


def test_only_claudes_matching_bet_without_a_note_is_matched(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    db_session.add_all([
        UserProfile(id=1, name="Marcus"), UserProfile(id=3, name="Claude"),
        Team(id=1, name="Dallas Cowboys", abbreviation="Cowboys", sport="nfl"),
        Team(id=2, name="Tampa Bay Buccaneers", abbreviation="Buccaneers", sport="nfl"),
        Team(id=3, name="Detroit Lions", abbreviation="Lions", sport="nfl")])
    db_session.flush()
    day = date(2026, 10, 8)
    db_session.add_all([
        Game(id=1, sport="nfl", season="2026", date=day, home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nfl", season="2026", date=day, home_team_id=1, away_team_id=3, status="final")])
    db_session.flush()

    def bet(pid, uid, gid, note=None):
        db_session.add(PaperPick(id=pid, user_id=uid, game_id=gid, pick_type="spread",
                                 pick_value="AWAY +9", odds=-109, stake=100, note=note))

    bet(1, 3, 1)              # Claude, the right game -> matched
    bet(2, 1, 1)              # Marcus on the same game -> never
    bet(3, 3, 2)              # Claude, another game -> no
    db_session.commit()
    assert [(b.id, n) for b, n in match_bets(db_session, [PICK], day)] == [
        (1, "Mayfield is out. Confidence 2/5 · Model: no side.")]
    db_session.get(PaperPick, 1).note = "already there"
    db_session.commit()
    assert match_bets(db_session, [PICK], day) == []        # never overwrites
```

(If `UserProfile` requires more columns than `id`/`name` — e.g. `starting_balance` with no default — add them with the model's defaults; check `backend/models.py` `class UserProfile`.)

Run: `.venv/Scripts/python -m pytest backend/tests/test_backfill_claude_notes.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.scripts.backfill_claude_notes`.

- [ ] **Step 2: Implement**

`backend/scripts/backfill_claude_notes.py`:

```python
"""One-off (2026-10-09): put Claude's written reasoning onto its paper bets.

Claude's NFL picks were written with reasoning at pick time and saved to
logs-archive/claude-picks-<date>-<slate>.json; before paper_picks.note
existed it never reached the app. This matches each saved pick to Claude's
(user 3) straight bet on that game and day and writes the note -- only where
a bet has none, so it never overwrites. Matching is by team nickname (the
last word of each team's full name) against the file's "Away @ Home".

    python -m backend.scripts.backfill_claude_notes                # dry run
    python -m backend.scripts.backfill_claude_notes --apply
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import aliased

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, PaperPick, Team

CLAUDE_USER_ID = 3
FILES = Path("logs-archive")
DATE_IN_NAME = re.compile(r"claude-picks-(\d{4}-\d{2}-\d{2})-")


def note_for(pick: dict) -> str:
    return f"{pick['why'].strip()} Confidence {pick['confidence']} · Model: {pick['vs_model']}."


def _nickname(name: str) -> str:
    return name.strip().split()[-1].lower()


def match_bets(session, picks: list[dict], day: date) -> list[tuple[PaperPick, str]]:
    Home, Away = aliased(Team), aliased(Team)
    rows = (session.query(PaperPick, Home, Away)
            .join(Game, Game.id == PaperPick.game_id)
            .join(Home, Home.id == Game.home_team_id)
            .join(Away, Away.id == Game.away_team_id)
            .filter(PaperPick.user_id == CLAUDE_USER_ID, PaperPick.parlay_id.is_(None),
                    PaperPick.note.is_(None), Game.date == day)
            .order_by(PaperPick.id).all())
    out = []
    for pick in picks:
        away, _, home = pick["game"].partition(" @ ")
        hits = [b for b, h, a in rows
                if _nickname(h.name) == _nickname(home) and _nickname(a.name) == _nickname(away)]
        if len(hits) == 1:
            out.append((hits[0], note_for(pick)))
        else:
            print(f"skip {pick['game']}: {len(hits)} matching bets")
    return out


def main(argv: list[str]) -> int:
    config = load_config("config.yaml")
    session = get_session(get_engine(config["database_path"]))
    try:
        matched = []
        for path in sorted(FILES.glob("claude-picks-*.json")):
            m = DATE_IN_NAME.search(path.name)
            if not m:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            matched += match_bets(session, data["picks"], date.fromisoformat(m.group(1)))
        for bet, note in matched:
            print(f"bet {bet.id} game {bet.game_id} {bet.pick_value}: {note[:80]}...")
        print(f"{len(matched)} bet(s) to annotate")
        if "--apply" in argv and matched:
            for bet, note in matched:
                bet.note = note
            session.commit()
            print("applied")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

Run: `.venv/Scripts/python -m pytest backend/tests/test_backfill_claude_notes.py -q`
Expected: PASS.

- [ ] **Step 3: The procedure sends the note from now on**

In `logs-archive/claude-picks-procedure.md`, step 5, change the JSON line to:

```
   JSON {"game_id":..,"pick_type":"spread|moneyline|over_under","side":"HOME|AWAY|Over|Under","stake":100,
   "note":"<the 2-3 sentence reasoning> Confidence <n>/5 · Model: <agree|against|no side>."}.
   The note shows on the bet's ticket and in the league feed (≤ 1000 characters; sources stay in the email).
```

- [ ] **Step 4: Mutation check**

In `match_bets`, drop `PaperPick.user_id == CLAUDE_USER_ID,` → the test FAILS (Marcus's bet matches too: 2 hits, or the wrong one). Restore; delete `backend/scripts/__pycache__`.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/backfill_claude_notes.py backend/tests/test_backfill_claude_notes.py
git commit -m "feat(bets): backfill Claude's saved reasoning onto its bets; the procedure sends notes from now on

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Add `logs-archive/claude-picks-procedure.md` only if it is tracked.)

---

### Task 9: Visual check at phone and desktop width

No code unless the check finds a fault (fix in `sportsbook.css`, own commit).

- [ ] **Step 1:** In the worktree: `cd frontend && npm run build`. Snapshot the live db with `sqlite3.backup` (never `cp`: WAL) to `<scratchpad>/lh.db`. Run the snapshot's migrations by starting uvicorn on :8001 from the worktree (`ENABLE_SCHEDULER=0`, `DATABASE_PATH=<scratchpad>\lh.db`; create_app runs `run_migrations`). Health check `/openapi.json` → 200.
- [ ] **Step 2:** In Chrome (claude-in-chrome), at desktop and in a 375px iframe (the window cannot narrow below ~500px):
  - Lobby: NFL first; chips All / This week / Next week (as dates allow); "Next week" shows next week's NFL; day headers "Tomorrow · n games"; `scrollWidth <= 375`.
  - Strip: "Why?" opens the panel; numbers + note; no NaN/null.
  - A game page with a model pick shows "Why the model likes …".
  - On the snapshot, run `python -m backend.scripts.backfill_claude_notes --apply` with `DATABASE_PATH` pointed at the snapshot (edit nothing in config.yaml: the script reads `config.yaml`'s `database_path` — if that is the live db, instead run it via a one-line python that calls `match_bets` on a snapshot session), then Claude's player page shows the note on the TNF ticket and the feed shows it under the bet.
- [ ] **Step 3:** Stop only the uvicorn whose command line has `--port 8001`; delete the snapshot files.

Expected: all hold; screenshots saved for the report.

---

### Task 10: Why does MMA have no information? (investigation, no code)

**Files:**
- Create or append: `docs/FINDINGS.md` (dated section "2026-10-09 — MMA picks at model_prob 0.5")

- [ ] **Step 1:** Measure on a read-only connection to the live db: per sport, published and tracking picks since 2026-09-01 with `model_prob = 0.5`; for MMA, how many fighters in those games have any `fighter`/Elo/stat rows (find the tables `_build_fighter_stats` reads in `backend/pipeline/pick_generator.py`), and when MMA games were first created (odds feed vs ESPN).
- [ ] **Step 2:** Trace `CombatSportsStrategy.predict` (grep `class CombatSportsStrategy`) to the line that yields 0.5 when stats are missing; note whether it is a default, a fallback, or a cold-start Elo (both fighters at the seed rating).
- [ ] **Step 3:** Write the finding: the cause with file:line, the counts (with dates and n), and 2-3 options to fix it with their cost — no fix is made. State plainly what was NOT checked.
- [ ] **Step 4:** Commit `docs/FINDINGS.md`:

```bash
git add docs/FINDINGS.md
git commit -m "docs(findings): why MMA picks have no information (model_prob 0.5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Merge notes (for "merge", not the build)

A migration (`paper_picks.note`) and scheduler changes:
1. Check ET and that no game the scheduler is grading is mid-window; save `scheduler.log` aside.
2. Stop the scheduler (both processes) and uvicorn :8000.
3. Back up the live db with `sqlite3.backup` (`sports_picks.backup-<stamp>-pre-pick-reasoning.db`).
4. `git merge --no-ff feat/lobby-horizon-and-pick-reasoning`; `npm --prefix frontend run build`.
5. Start uvicorn (`scripts/start_site.ps1` starts it if absent; migrations run at startup) and the scheduler (`Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\start_scheduler.ps1' -WindowStyle Hidden`).
6. Verify the column exists, `/paper/board?days=14` returns reasoning, the tunnel serves the new js.
7. One-offs, each dry run first, then `--apply` only if the dry run lists what is expected: `python -m backend.scripts.demote_no_information_picks`, `python -m backend.scripts.backfill_claude_notes`.
8. Next `price_refresh` (every 3h on :30) should log NFL among its sports.
