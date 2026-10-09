# Sportsbook Phase 7 — Player View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tapping a name on Leaders opens that player's page — their balance, profit, current and best streak, results by period (today / this week / this month / all time) and every bet they have placed — restoring what retiring the old Paper Trading page removed in Phase 5.

**Architecture:** Frontend only. Every number already has an endpoint: `GET /users/` (balance, profit, streaks), `GET /users/{id}/stats` (period results) and `GET /users/{id}/bets` (tickets, the same ones My Bets shows). My Bets' Open/Settled ticket list is extracted into a shared `TicketList` so the player page and My Bets render tickets the same way; the player page passes no `userId`, which is what keeps the Cash out button off other players' bets. A new route `/players/:id` hosts the page; Leaders rows link to it.

**Tech Stack:** React 19, react-router-dom, @tanstack/react-query 5, vitest + Testing Library, TypeScript (`tsc -b`).

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md` (§10 Leaders; "Visibility: all bets and stakes are visible to everyone in the league"). Phase 7 is not one of the spec's six phases: it is the owner's accepted follow-up (2026-10-09) restoring the period stats, other players' history and best streak that Phase 5 removed with `PaperTrading.tsx` (last seen at `abbaa35^:frontend/src/pages/PaperTrading.tsx`). The **Design** section below is binding where the spec is silent.

## Design

- **Route:** `/players/:id`. Not a player (non-numeric id, unknown id, a deleted player) → "No such player." with a link back to Leaders. The player list failing to load → an alert, not "No such player".
- **Header:** initial avatar + name (`h1`). A money strip (`dl.sb-money`, aria-label "`{name}`'s money"): **Balance** (`current_balance`, the settled balance), **Profit** (signed), **Streak** (`W4` / `L2` / `—`, straight bets only, as Leaders), **Best win streak** (`W6` / `—`).
- **Note under the strip:** "Leaders ranks single bets only; these figures include parlays." — the page's profit and ROI include parlays (`/users/` and `/stats` use `player_bets(include_parlays=True)`), the board's do not, so the two pages legitimately differ.
- **Your own page:** the same read-only view, plus "This is you — cash out and track your open bets in My Bets." with a link to `/bets`.
- **Results:** table Period | W-L | Win % | Profit | ROI for Today / This week / This month / All time, from `/users/{id}/stats`. A period with no settled bets shows `—` in every cell; Win % is `—` when nothing was decided (only pushes / cash outs). When `all_time.cashed_out > 0`: "Profit and ROI include N cashed-out bet(s); W-L does not."
- **Bets:** the shared `TicketList` (Open / Settled tabs, Won/Lost filter) over `/users/{id}/bets` tickets, with no `userId` → no Cash out button. Empty Open tab: "`{name}` has no open bets."
- **Leaders:** each player's name links to `/players/{id}`; the Model row links to `/track-record` (the Model has no player page).
- **FAQ:** two answers still describe the retired Paper Trading page ("How do I join?", "How do I place a pick?"); both are rewritten for the sportsbook UI, and the second says where a player's page is.
- **Merge:** no backend change, so no migration and no process restart; `frontend/dist` is served from disk, so the rebuild is the deploy.

## Global Constraints

- All bets and stakes are visible to everyone in the league (spec §10) — showing another player's tickets is intended; acting on them (cash out) is not.
- Styles go in `frontend/src/sportsbook.css` (imported by `main.tsx`); `App.css` is never imported.
- Money is shown with `formatMoney` / `signedMoney` (`frontend/src/lib/board.ts`, `frontend/src/lib/bets.ts`); negatives use U+2212 "−", not a hyphen.
- No new dependencies.
- Product name in any display string is "Metric Edge".
- Layout works at 375px wide with no horizontal page scroll.
- Frontend test command (run from the worktree root): `sh -c "cd frontend && npx vitest run && npx tsc -b"`. Run vitest from `frontend/`, never via `npm --prefix` from the repo root (it picks up the wrong config: 108 false failures).
- A fresh worktree needs `cd frontend && npm ci --legacy-peer-deps` (plain `npm ci` fails with ERESOLVE).

## Review Focus

1. **A link that is not a player** (`/players/abc`, `/players/999`, a deleted player): "No such player." and a way back — never an endless "Loading…", a crash, or requests for `/users/NaN/...`. Pinned in Task 3.
2. **Someone else's open bet that has a cash-out offer:** no Cash out button — only the owner may cash out, and the request would fail on the PIN anyway. Pinned in Task 1 (component) and Task 3 (page).
3. **A player with no settled or no bets at all:** dashes in every period cell and "`{name}` has no open bets." — never "0-0", "0%", "NaN%" or My Bets' "tap a price in the Lobby" (which speaks to the viewer). Pinned in Tasks 2 and 3.
4. **The Model row** (id `null`): links to Track record, never `/players/null`. Pinned in Task 3.
5. **Leaders vs the player page disagree** (straight-only vs all bets): the page says why. Pinned in Task 3.

---

### Task 1: Shared `TicketList` (My Bets refactor)

**Files:**
- Create: `frontend/src/components/TicketList.tsx`
- Create: `frontend/src/components/TicketList.test.tsx`
- Modify: `frontend/src/pages/MyBets.tsx` (whole file replaced below)

**Interfaces:**
- Consumes: `TicketCard` (`{ t: Ticket; userId?: number }`; `userId` present + open ticket → `CashOutButton`), `splitTickets`, `filterSettled`, `ticketKey`, `SettledFilter` from `lib/bets.ts`.
- Produces: `export default function TicketList(props: { tickets: Ticket[] | undefined; userId?: number; loading?: boolean; loadingText?: string; emptyOpen: string })`. Settled empty text is always "No settled bets here yet."; `loadingText` defaults to "Loading bets…".

- [ ] **Step 1: Write the failing test**

`frontend/src/components/TicketList.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import TicketList from './TicketList'
import type { Ticket, TicketLeg } from '../types'

const leg: TicketLeg = {
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null, result: null,
  game: { id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2099-10-09T00:15:00+00:00',
    status: 'scheduled', home_score: null, away_score: null, live_detail: null },
}
const t = (over: Partial<Ticket>): Ticket => ({
  kind: 'straight', id: 1, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg], ...over,
})

function renderList(props: Parameters<typeof TicketList>[0]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><TicketList {...props} /></MemoryRouter></QueryClientProvider>)
}

describe('TicketList', () => {
  it("puts a Cash out button only on the viewer's own open tickets (Review Focus 2)", () => {
    const tickets = [t({ id: 9, cash_out: { available: true, offer: 90.68 } })]
    const { unmount } = renderList({ tickets, userId: 1, emptyOpen: 'none' })
    expect(screen.getByRole('button', { name: 'Cash out $90.68' })).toBeInTheDocument()
    unmount()
    renderList({ tickets, emptyOpen: 'none' })
    expect(screen.getByRole('article', { name: 'Bet #P-9' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cash out/ })).toBeNull()
  })
  it('shows the caller\'s empty text on Open and the shared one on Settled', () => {
    renderList({ tickets: [], emptyOpen: 'Sam has no open bets.' })
    expect(screen.getByText('Sam has no open bets.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Settled' }))
    expect(screen.getByText('No settled bets here yet.')).toBeInTheDocument()
  })
  it('shows the loading text, not an empty message, before tickets arrive', () => {
    renderList({ tickets: undefined, loading: true, loadingText: 'Loading your bets…', emptyOpen: 'none' })
    expect(screen.getByText('Loading your bets…')).toBeInTheDocument()
    expect(screen.queryByText('none')).toBeNull()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `sh -c "cd frontend && npx vitest run src/components/TicketList.test.tsx"`
Expected: FAIL — `Failed to resolve import "./TicketList"`.

- [ ] **Step 3: Write `TicketList.tsx`**

`frontend/src/components/TicketList.tsx` (the tabs, filter and list moved verbatim out of `MyBets.tsx`):

```tsx
import { useState } from 'react'
import TicketCard from './TicketCard'
import { filterSettled, splitTickets, ticketKey, type SettledFilter } from '../lib/bets'
import type { Ticket } from '../types'

const FILTERS: [SettledFilter, string][] = [['all', 'All'], ['won', 'Won'], ['lost', 'Lost']]

/** Open / Settled tabs over one player's tickets: My Bets and a player's page.
 *  Pass ``userId`` only for the viewer's own bets -- it is what puts a Cash out
 *  button on an open ticket. */
export default function TicketList({ tickets, userId, loading = false, loadingText = 'Loading bets…', emptyOpen }: {
  tickets: Ticket[] | undefined; userId?: number; loading?: boolean; loadingText?: string; emptyOpen: string
}) {
  const [tab, setTab] = useState<'open' | 'settled'>('open')
  const [filter, setFilter] = useState<SettledFilter>('all')
  const { open, settled } = splitTickets(tickets ?? [])
  const shown = tab === 'open' ? open : filterSettled(settled, filter)

  return (
    <>
      <div className="sb-sport-tabs" role="tablist" aria-label="Bets">
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'open'} onClick={() => setTab('open')}>
          Open ({open.length})
        </button>
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'settled'} onClick={() => setTab('settled')}>
          Settled
        </button>
      </div>
      {tab === 'settled' && (
        <div className="sb-filter" role="group" aria-label="Filter settled bets">
          {FILTERS.map(([f, label]) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{label}</button>
          ))}
        </div>
      )}
      {loading && <p className="sb-empty">{loadingText}</p>}
      {tickets && shown.length === 0 && (
        <p className="sb-empty">{tab === 'open' ? emptyOpen : 'No settled bets here yet.'}</p>
      )}
      {shown.map(t => <TicketCard key={ticketKey(t)} t={t} userId={userId} />)}
    </>
  )
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `sh -c "cd frontend && npx vitest run src/components/TicketList.test.tsx"`
Expected: PASS, 3 tests.

- [ ] **Step 5: Make My Bets use it**

Replace `frontend/src/pages/MyBets.tsx` with:

```tsx
import TicketList from '../components/TicketList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { signedMoney } from '../lib/bets'
import { formatMoney } from '../lib/board'

export default function MyBets() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)

  if (!me) {
    return <div className="sb-bets"><p className="sb-empty">Choose a player in the top bar, or join the league, to see your bets.</p></div>
  }
  const data = bets.data
  const pl = data?.summary.today_pl ?? 0

  return (
    <div className="sb-bets">
      {data && (
        <dl className="sb-money" aria-label="Your money">
          <div><dt>Available</dt><dd>{formatMoney(data.summary.available)}</dd></div>
          <div><dt>Settled balance</dt><dd>{formatMoney(data.summary.balance)}</dd></div>
          <div><dt>Open stakes</dt><dd>{formatMoney(data.summary.open_stakes)}</dd></div>
          <div><dt>Today's P/L</dt>
            <dd className={pl > 0 ? 'sb-up' : pl < 0 ? 'sb-down' : undefined}>{signedMoney(pl)}</dd></div>
        </dl>
      )}
      {bets.isError && <div role="alert" className="sb-offline">Couldn't load your bets — retrying.</div>}
      <TicketList tickets={data?.tickets} userId={me.id} loading={bets.isLoading}
        loadingText="Loading your bets…" emptyOpen="No open bets — tap a price in the Lobby to start." />
    </div>
  )
}
```

- [ ] **Step 6: Run My Bets' existing tests — they guard the refactor**

Run: `sh -c "cd frontend && npx vitest run src/pages/MyBets.test.tsx src/components/TicketList.test.tsx"`
Expected: PASS, every existing My Bets test unchanged plus the 3 new ones.

- [ ] **Step 7: Mutation check**

In `MyBets.tsx` change `userId={me.id}` to nothing (delete the prop). Run `MyBets.test.tsx`; expected: "offers a cash out on an open ticket" FAILS. Restore, re-run, PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/TicketList.tsx frontend/src/components/TicketList.test.tsx frontend/src/pages/MyBets.tsx
git commit -m "refactor(bets): My Bets' ticket tabs become a shared TicketList; userId alone enables cash out

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Results-by-period table

**Files:**
- Modify: `frontend/src/types.ts` (`PeriodStats` gains `cashed_out`)
- Create: `frontend/src/hooks/usePlayerStats.ts`
- Create: `frontend/src/components/PeriodStatsTable.tsx`
- Create: `frontend/src/components/PeriodStatsTable.test.tsx`
- Modify: `frontend/src/sportsbook.css` (append `.sb-note`)

**Interfaces:**
- Consumes: `GET /users/{id}/stats` via `api.users.stats(id): Promise<UserStats>` (exists). Each period is `{wins, losses, pushes, cashed_out, total, win_rate, profit, roi}` where `total` = settled bets (wins + losses + pushes + cash outs), `win_rate` and `roi` are **percent** numbers (`75`, `-7.09`) and 0 when undefined.
- Produces: `export default function PeriodStatsTable({ stats }: { stats: UserStats })` (table `aria-label="Results by period"`, each row `aria-label` = "Today" | "This week" | "This month" | "All time"); `export function usePlayerStats(userId: number)` (react-query, key `['users', 'stats', userId]`); CSS class `.sb-note`.

- [ ] **Step 1: Add `cashed_out` to the type**

In `frontend/src/types.ts`, in `interface PeriodStats`, after `pushes: number;` add:

```ts
  /** Settled by cash out: in total, profit and ROI; not in W-L or win rate. */
  cashed_out: number;
```

- [ ] **Step 2: Write the failing test**

`frontend/src/components/PeriodStatsTable.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PeriodStatsTable from './PeriodStatsTable'
import type { PeriodStats, UserStats } from '../types'

const p = (over: Partial<PeriodStats> = {}): PeriodStats => ({
  wins: 0, losses: 0, pushes: 0, cashed_out: 0, total: 0, win_rate: 0, profit: 0, roi: 0, ...over })
const stats = (over: Partial<UserStats> = {}): UserStats => ({
  today: p(), this_week: p(), this_month: p(), all_time: p(), daily_breakdown: [], ...over })
const cells = (name: string) =>
  Array.from(screen.getByRole('row', { name }).querySelectorAll('td')).map(td => td.textContent)

describe('PeriodStatsTable', () => {
  it("shows each period's record, win rate, profit and ROI", () => {
    render(<PeriodStatsTable stats={stats({
      this_week: p({ wins: 3, losses: 1, pushes: 1, total: 5, win_rate: 75, profit: 182.4, roi: 36.48 }),
      all_time: p({ wins: 8, losses: 9, total: 17, win_rate: 47.1, profit: -120.5, roi: -7.09 }) })} />)
    expect(cells('This week')).toEqual(['This week', '3-1-1', '75%', '+$182.40', '+36.48%'])
    expect(cells('All time')).toEqual(['All time', '8-9', '47.1%', '−$120.50', '−7.09%'])
  })
  it('shows dashes for a period with no settled bets, and for win rate with nothing decided (Review Focus 3)', () => {
    render(<PeriodStatsTable stats={stats({ this_month: p({ pushes: 2, total: 2 }) })} />)
    expect(cells('Today')).toEqual(['Today', '—', '—', '—', '—'])
    expect(cells('This month')).toEqual(['This month', '0-0-2', '—', '$0.00', '0%'])
  })
  it('notes cashed-out bets only when there are some', () => {
    const { unmount } = render(<PeriodStatsTable stats={stats({
      all_time: p({ wins: 1, cashed_out: 2, total: 3, win_rate: 100, profit: 50, roi: 10 }) })} />)
    expect(screen.getByText('Profit and ROI include 2 cashed-out bets; W-L does not.')).toBeInTheDocument()
    unmount()
    render(<PeriodStatsTable stats={stats({ all_time: p({ wins: 1, cashed_out: 1, total: 2, win_rate: 100 }) })} />)
    expect(screen.getByText('Profit and ROI include 1 cashed-out bet; W-L does not.')).toBeInTheDocument()
  })
  it('has no note without cash outs', () => {
    render(<PeriodStatsTable stats={stats({ all_time: p({ wins: 1, total: 1, win_rate: 100 }) })} />)
    expect(screen.queryByText(/cashed-out/)).toBeNull()
  })
})
```

- [ ] **Step 3: Run it to verify it fails**

Run: `sh -c "cd frontend && npx vitest run src/components/PeriodStatsTable.test.tsx"`
Expected: FAIL — `Failed to resolve import "./PeriodStatsTable"`.

- [ ] **Step 4: Write the hook and the component**

`frontend/src/hooks/usePlayerStats.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Under ['users', ...] like useMyBets, so a placed or cashed-out bet (which
// invalidates ['users']) refreshes a player's results too.
export function usePlayerStats(userId: number) {
  return useQuery({
    queryKey: ['users', 'stats', userId],
    queryFn: () => api.users.stats(userId),
    refetchInterval: 60_000,
  })
}
```

`frontend/src/components/PeriodStatsTable.tsx`:

```tsx
import { signedMoney } from '../lib/bets'
import type { PeriodStats, UserStats } from '../types'

const PERIODS: [Exclude<keyof UserStats, 'daily_breakdown'>, string][] = [
  ['today', 'Today'], ['this_week', 'This week'], ['this_month', 'This month'], ['all_time', 'All time']]

const record = (s: PeriodStats) => `${s.wins}-${s.losses}${s.pushes ? `-${s.pushes}` : ''}`
// The endpoint sends percents (36.48, -7.09); a minus is U+2212 like signedMoney.
const signedPct = (n: number) => (n > 0 ? `+${n}%` : n < 0 ? `−${Math.abs(n)}%` : '0%')
const tone = (n: number) => (n > 0 ? 'sb-up' : n < 0 ? 'sb-down' : undefined)

/** A player's results for today, this week, this month and all time
 *  (`/users/{id}/stats`; days are game dates in ET, weeks start Monday). */
export default function PeriodStatsTable({ stats }: { stats: UserStats }) {
  const cashed = stats.all_time.cashed_out
  return (
    <>
      <table className="sb-lead-table" aria-label="Results by period">
        <thead><tr><th>Period</th><th>W-L</th><th>Win %</th><th>Profit</th><th>ROI</th></tr></thead>
        <tbody>
          {PERIODS.map(([key, label]) => {
            const s = stats[key]
            const none = s.total === 0
            return (
              <tr key={key} aria-label={label}>
                <td>{label}</td>
                <td>{none ? '—' : record(s)}</td>
                <td>{none || s.wins + s.losses === 0 ? '—' : `${s.win_rate}%`}</td>
                <td className={none ? undefined : tone(s.profit)}>{none ? '—' : signedMoney(s.profit)}</td>
                <td className={none ? undefined : tone(s.roi)}>{none ? '—' : signedPct(s.roi)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {cashed > 0 && (
        <p className="sb-note">Profit and ROI include {cashed} cashed-out {cashed === 1 ? 'bet' : 'bets'}; W-L does not.</p>
      )}
    </>
  )
}
```

Append to `frontend/src/sportsbook.css`:

```css
.sb-note { color: var(--text-muted); font-size: 0.75rem; margin: 0.4rem 0 0.75rem; }
```

- [ ] **Step 5: Run it to verify it passes**

Run: `sh -c "cd frontend && npx vitest run src/components/PeriodStatsTable.test.tsx && npx tsc -b"`
Expected: PASS, 4 tests; tsc clean (if tsc reports a test fixture missing `cashed_out`, add `cashed_out: 0` to that fixture — the server always sends it).

- [ ] **Step 6: Mutation check**

Change `none || s.wins + s.losses === 0` to `none`. Run the file; expected: "shows dashes…" FAILS (`0%` where `—` expected). Restore; PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/hooks/usePlayerStats.ts frontend/src/components/PeriodStatsTable.tsx frontend/src/components/PeriodStatsTable.test.tsx frontend/src/sportsbook.css
git commit -m "feat(players): results-by-period table with dashes for empty periods and a cash-out note

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The player page, its route, Leaders links and the FAQ

**Files:**
- Create: `frontend/src/pages/PlayerView.tsx`
- Create: `frontend/src/pages/PlayerView.test.tsx`
- Modify: `frontend/src/App.tsx` (route)
- Modify: `frontend/src/pages/Leaders.tsx` (`Row` name cell)
- Modify: `frontend/src/pages/Leaders.test.tsx` (one test)
- Modify: `frontend/src/pages/FAQ.tsx` (two answers)
- Modify: `frontend/src/sportsbook.css` (append `.sb-lead-name`, `.sb-player h1`)

**Interfaces:**
- Consumes: `TicketList` (Task 1), `PeriodStatsTable` + `usePlayerStats` (Task 2), `useMyBets(userId)` (key `['users','bets',id]`), `useCurrentPlayer()`, `api.users.list` (key `['users','list']`, shared with Leaders and the top bar), `UserProfile` (`current_balance`, `profit`, `current_streak`, `best_streak`, `streak_type: 'win' | 'loss' | 'none'`; `best_streak` is the best **win** streak, straight bets only — `backend/paper/feed.update_streaks`).
- Produces: `export default function PlayerView()` at route `players/:id`.

- [ ] **Step 1: Write the failing page test**

`frontend/src/pages/PlayerView.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import PlayerView from './PlayerView'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { PeriodStats, Ticket, TicketLeg, UserProfile, UserStats } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    users: { ...actual.api.users, list: vi.fn(), stats: vi.fn(), bets: vi.fn() } } }
})

const user = (over: Partial<UserProfile>): UserProfile => ({
  id: 2, name: 'Sam', starting_balance: 10000, current_balance: 10240.5, available_balance: 10140.5,
  total_wagered: 900, profit: 240.5, roi: 26.7, wins: 8, losses: 4, pushes: 0, pending: 1, win_rate: 66.7,
  current_streak: 4, best_streak: 6, streak_type: 'win', ...over })
const p = (over: Partial<PeriodStats> = {}): PeriodStats => ({
  wins: 0, losses: 0, pushes: 0, cashed_out: 0, total: 0, win_rate: 0, profit: 0, roi: 0, ...over })
const stats = (over: Partial<UserStats> = {}): UserStats => ({
  today: p(), this_week: p(), this_month: p(), all_time: p(), daily_breakdown: [], ...over })
const leg: TicketLeg = {
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null, result: null,
  game: { id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2099-10-09T00:15:00+00:00',
    status: 'scheduled', home_score: null, away_score: null, live_detail: null } }
const t = (over: Partial<Ticket>): Ticket => ({
  kind: 'straight', id: 1, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg], ...over })

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/players/:id" element={<PlayerView />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useUserStore.setState({ currentUserName: 'Me' })
  vi.mocked(api.users.list).mockResolvedValue([
    user({ id: 1, name: 'Me', current_streak: 2, streak_type: 'loss', best_streak: 3 }), user({})])
  vi.mocked(api.users.stats).mockResolvedValue(stats({
    all_time: p({ wins: 8, losses: 4, total: 12, win_rate: 66.7, profit: 240.5, roi: 26.72 }) }))
  vi.mocked(api.users.bets).mockResolvedValue({
    summary: { available: 10140.5, balance: 10240.5, open_stakes: 100, today_pl: 0 },
    tickets: [t({ id: 6, cash_out: { available: true, offer: 90.68 } }), t({ id: 5, result: 'win', payout: 91.74 })] })
})

describe('PlayerView', () => {
  it("shows a player's money, streaks, results by period and bets", async () => {
    renderAt('/players/2')
    expect(await screen.findByRole('heading', { level: 1, name: 'Sam' })).toBeInTheDocument()
    const money = screen.getByLabelText("Sam's money")
    expect(money).toHaveTextContent('$10,240.50')
    expect(money).toHaveTextContent('+$240.50')
    expect(money).toHaveTextContent('W4')
    expect(money).toHaveTextContent('W6')
    expect(await screen.findByRole('row', { name: 'All time' })).toHaveTextContent('8-4')
    expect(await screen.findByRole('article', { name: 'Bet #P-6' })).toBeInTheDocument()
    expect(api.users.stats).toHaveBeenCalledWith(2)
    expect(api.users.bets).toHaveBeenCalledWith(2)
  })
  it("never offers a cash out on someone else's bet (Review Focus 2)", async () => {
    renderAt('/players/2')
    expect(await screen.findByRole('article', { name: 'Bet #P-6' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cash out/ })).toBeNull()
  })
  it('on your own page, shows a losing streak and points to My Bets for cashing out', async () => {
    renderAt('/players/1')
    expect(await screen.findByRole('link', { name: 'My Bets' })).toHaveAttribute('href', '/bets')
    expect(screen.getByLabelText("Me's money")).toHaveTextContent('L2')
    expect(await screen.findByRole('article', { name: 'Bet #P-6' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cash out/ })).toBeNull()
  })
  it.each(['/players/abc', '/players/999'])('says %s is not a player, with a way back (Review Focus 1)', async (path) => {
    renderAt(path)
    expect(await screen.findByText(/No such player/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to Leaders' })).toHaveAttribute('href', '/leaders')
    expect(api.users.stats).not.toHaveBeenCalled()
    expect(api.users.bets).not.toHaveBeenCalled()
  })
  it('says the players could not be loaded rather than "No such player"', async () => {
    vi.mocked(api.users.list).mockRejectedValue(new Error('offline'))
    renderAt('/players/2')
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load players")
    expect(screen.queryByText(/No such player/)).toBeNull()
  })
  it('shows dashes and "has no open bets" for a player with no bets (Review Focus 3)', async () => {
    vi.mocked(api.users.list).mockResolvedValue([user({ current_streak: 0, streak_type: 'none', best_streak: 0,
      current_balance: 10000, profit: 0 })])
    vi.mocked(api.users.stats).mockResolvedValue(stats())
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 10000, balance: 10000, open_stakes: 0, today_pl: 0 }, tickets: [] })
    renderAt('/players/2')
    expect(await screen.findByText('Sam has no open bets.')).toBeInTheDocument()
    expect(await screen.findByRole('row', { name: 'All time' })).toHaveTextContent('All time————')
    expect(screen.queryByText(/tap a price in the Lobby/)).toBeNull()
  })
  it('explains why its numbers differ from Leaders (Review Focus 5)', async () => {
    renderAt('/players/2')
    expect(await screen.findByText('Leaders ranks single bets only; these figures include parlays.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `sh -c "cd frontend && npx vitest run src/pages/PlayerView.test.tsx"`
Expected: FAIL — `Failed to resolve import "./PlayerView"`.

- [ ] **Step 3: Write the page**

`frontend/src/pages/PlayerView.tsx`:

```tsx
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import PeriodStatsTable from '../components/PeriodStatsTable'
import TicketList from '../components/TicketList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { usePlayerStats } from '../hooks/usePlayerStats'
import { signedMoney } from '../lib/bets'
import { formatMoney } from '../lib/board'
import type { UserProfile } from '../types'

function streakText(u: UserProfile): string {
  if (!u.current_streak || u.streak_type === 'none') return '—'
  return `${u.streak_type === 'win' ? 'W' : 'L'}${u.current_streak}`
}

function PlayerBody({ player, isMe }: { player: UserProfile; isMe: boolean }) {
  const stats = usePlayerStats(player.id)
  const bets = useMyBets(player.id)
  return (
    <div className="sb-bets sb-player">
      <h1 className="sb-page-title">
        <span className="sb-avatar" aria-hidden="true">{player.name.charAt(0).toUpperCase()}</span>{player.name}
      </h1>
      <dl className="sb-money" aria-label={`${player.name}'s money`}>
        <div><dt>Balance</dt><dd>{formatMoney(player.current_balance)}</dd></div>
        <div><dt>Profit</dt>
          <dd className={player.profit > 0 ? 'sb-up' : player.profit < 0 ? 'sb-down' : undefined}>{signedMoney(player.profit)}</dd></div>
        <div><dt>Streak</dt><dd>{streakText(player)}</dd></div>
        <div><dt>Best win streak</dt><dd>{player.best_streak ? `W${player.best_streak}` : '—'}</dd></div>
      </dl>
      <p className="sb-note">Leaders ranks single bets only; these figures include parlays.</p>
      {isMe && (
        <p className="sb-note">This is you — cash out and track your open bets in <Link className="sb-link" to="/bets">My Bets</Link>.</p>
      )}
      <h2 className="sb-day">Results</h2>
      {stats.isError && <div role="alert" className="sb-offline">Couldn't load results — retrying.</div>}
      {stats.data && <PeriodStatsTable stats={stats.data} />}
      <h2 className="sb-day">Bets</h2>
      {bets.isError && <div role="alert" className="sb-offline">Couldn't load bets — retrying.</div>}
      {/* No userId: another player's bets are visible to everyone (spec §10) but only the owner cashes out. */}
      <TicketList tickets={bets.data?.tickets} loading={bets.isLoading} emptyOpen={`${player.name} has no open bets.`} />
    </div>
  )
}

/** A player's page (`/players/:id`), opened from Leaders. */
export default function PlayerView() {
  const { id } = useParams()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const me = useCurrentPlayer()
  const playerId = /^\d+$/.test(id ?? '') ? Number(id) : undefined
  const player = playerId === undefined ? undefined : users.data?.find(u => u.id === playerId)

  if (users.isError && !users.data) {
    return <div className="sb-bets"><div role="alert" className="sb-offline">Couldn't load players — retrying.</div></div>
  }
  if (users.isLoading) return <div className="sb-bets"><p className="sb-empty">Loading…</p></div>
  if (!player) {
    return (
      <div className="sb-bets">
        <p className="sb-empty">No such player. <Link className="sb-link" to="/leaders">Back to Leaders</Link></p>
      </div>
    )
  }
  // Keyed by id so moving between two players' pages resets the tabs and filter.
  return <PlayerBody key={player.id} player={player} isMe={me?.id === player.id} />
}
```

Add the route in `frontend/src/App.tsx`: the import after `import Leaders from './pages/Leaders';`

```tsx
import PlayerView from './pages/PlayerView';
```

and the route after `<Route path="leaders" element={<Leaders />} />`:

```tsx
                <Route path="players/:id" element={<PlayerView />} />
```

- [ ] **Step 4: Run the page test**

Run: `sh -c "cd frontend && npx vitest run src/pages/PlayerView.test.tsx"`
Expected: PASS, 8 tests (the `it.each` counts as 2).

- [ ] **Step 5: Write the failing Leaders test**

In `frontend/src/pages/Leaders.test.tsx`, inside `describe('Leaders', …)`, after the "does not pin your row…" test, add:

```tsx
  it('links each player to their page and the Model to its track record (Review Focus 4)', async () => {
    renderPage()
    expect(await screen.findByRole('link', { name: 'Sam' })).toHaveAttribute('href', '/players/2')
    expect(screen.getByRole('link', { name: 'Model' })).toHaveAttribute('href', '/track-record')
    expect(screen.getAllByRole('link', { name: 'Me' })[0]).toHaveAttribute('href', '/players/1')
  })
```

Run: `sh -c "cd frontend && npx vitest run src/pages/Leaders.test.tsx"`
Expected: FAIL — `Unable to find role="link" and name "Sam"`.

- [ ] **Step 6: Link the names on Leaders**

In `frontend/src/pages/Leaders.tsx` add `import { Link } from 'react-router-dom'` to the imports, and in `Row` replace

```tsx
      <td><span className="sb-avatar" aria-hidden="true">{r.name.charAt(0).toUpperCase()}</span>{r.name}</td>
```

with

```tsx
      <td>
        <span className="sb-avatar" aria-hidden="true">{r.name.charAt(0).toUpperCase()}</span>
        {/* The Model has no player page (id null): its record is Track record. */}
        <Link className="sb-lead-name" to={r.is_model || r.id === null ? '/track-record' : `/players/${r.id}`}>{r.name}</Link>
      </td>
```

Append to `frontend/src/sportsbook.css`:

```css
.sb-lead-name { color: inherit; font-weight: 600; text-decoration: none; }
.sb-lead-name:hover, .sb-lead-name:focus-visible { text-decoration: underline; }
.sb-player h1 { display: flex; align-items: center; }
```

Run: `sh -c "cd frontend && npx vitest run src/pages/Leaders.test.tsx"`
Expected: PASS, every test (the existing row tests match rows by aria-label, which is unchanged).

- [ ] **Step 7: Rewrite the two stale FAQ answers**

In `frontend/src/pages/FAQ.tsx`, replace the answer of "How do I join?" (the string starting `'Go to the Paper Trading page, type your name`) with:

```tsx
          'Tap the player button in the top bar and choose "+ Join the league", then type your name and choose a 4–6 digit PIN and tap "Join". Names are unique (ignoring capitals). Your PIN is needed for every bet; five wrong tries lock betting for 15 minutes. You start with $10,000.',
```

and the answer of "How do I place a pick?" (the string starting `'Click your name on the leaderboard to open your profile.`) with:

```tsx
          'Tap any price in the Lobby or on a game page to add it to your bet slip, set your stake, and place it; add more than one selection to make a parlay. The price comes from the sportsbooks and is checked again when you place the bet -- if it has moved, the slip shows the new price before anything is placed. The stake comes out of your Available balance straight away; your settled balance only changes when the bet settles. Bets close when the game starts. Your bets are in My Bets, and tapping anyone\u2019s name on Leaders shows their results and bets.',
```

Verify: `grep -c "Paper Trading page\|Place a Pick" frontend/src/pages/FAQ.tsx` → `0`.

- [ ] **Step 8: Full frontend suite + types**

Run: `sh -c "cd frontend && npx vitest run && npx tsc -b"`
Expected: all tests pass (238 before this phase + 3 + 4 + 8 + 1 = 254); tsc clean.

- [ ] **Step 9: Mutation checks**

Each one: apply, run the named file, confirm the named test FAILS, restore, confirm PASS.
1. `PlayerView.tsx`: add `userId={player.id}` to the `TicketList` → "never offers a cash out on someone else's bet" fails.
2. `PlayerView.tsx`: delete the `if (users.isError && !users.data) {…}` block → "says the players could not be loaded…" fails.
3. `Leaders.tsx`: change the `to=` expression to `` `/players/${r.id}` `` → "links each player…" fails (Model gets `/players/null`).
4. `PlayerView.tsx`: change `emptyOpen={`${player.name} has no open bets.`}` to `emptyOpen="No open bets — tap a price in the Lobby to start."` → "shows dashes and…" fails.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/PlayerView.tsx frontend/src/pages/PlayerView.test.tsx frontend/src/App.tsx frontend/src/pages/Leaders.tsx frontend/src/pages/Leaders.test.tsx frontend/src/pages/FAQ.tsx frontend/src/sportsbook.css
git commit -m "feat(players): a player's page from Leaders -- money, streaks, results by period, bets; FAQ updated

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Visual check at phone and desktop width

No new code unless the check finds a layout fault; a CSS fix goes in `sportsbook.css` with its own commit.

**Files:**
- Modify (only if needed): `frontend/src/sportsbook.css`

- [ ] **Step 1: Build in the worktree and serve a snapshot on :8001**

Never build in the main tree — `:8000` serves `frontend/dist` from disk, so a main-tree build is a deploy.

```bash
cd <worktree>/frontend && npm run build
/c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python -c "import sqlite3; s=sqlite3.connect('C:/Users/mwill/Documents/mwilliams2733/sports_picks/sports_picks.db'); d=sqlite3.connect('<scratchpad>/p7.db'); s.backup(d); d.close()"
```

Start from the worktree root (PowerShell, detached): `$env:ENABLE_SCHEDULER='0'; $env:DATABASE_PATH='<scratchpad>\p7.db'; Start-Process <main venv python> -ArgumentList '-m','uvicorn','backend.api.main:app','--host','127.0.0.1','--port','8001' -WindowStyle Hidden`. Health check `http://127.0.0.1:8001/openapi.json` → 200.

- [ ] **Step 2: Look at it with claude-in-chrome**

At 375×812 and at desktop width, open `http://127.0.0.1:8001/leaders`, tap a player's name, and check:
- the page opens at `/players/<id>` with name, money strip, note, Results table and Bets tabs;
- `document.documentElement.scrollWidth <= 375` at phone width (no sideways scroll), the Results table's five columns fit;
- Settled tab shows that player's settled tickets, with no Cash out button anywhere;
- the Model row goes to Track record; `/players/abc` shows "No such player.";
- your own page shows the My Bets line.

Expected: all hold. A layout fault → fix in `sportsbook.css`, re-run the suite, commit `fix(players): …`.

- [ ] **Step 3: Stop :8001 and delete the snapshot**

Stop only the uvicorn whose command line has `--port 8001` (check `Get-CimInstance Win32_Process` command lines — never the `:8000` server), then delete `<scratchpad>/p7.db*`.

---

## Merge notes (for the "merge" step, not the build)

Frontend only: no migration, no backend restart, no scheduler restart. `git merge --no-ff feat/sportsbook-phase-7`, then `npm --prefix frontend run build` in the main tree (that is the deploy), then confirm the tunnel serves the new `assets/index-*.js` and `/players/<id>` loads.
