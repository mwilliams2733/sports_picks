import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TodaysPicks from './TodaysPicks'
import { setOwnerKey } from '../lib/secrets'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import { ToastProvider } from '../components/Toast'
import type { PickData, GameOddsData } from '../types'

const ok = <T,>(data: T) => ({ data, error: null, isLoading: false })

// Mutable so individual tests can supply their own picks/games -- the mock
// factory itself only runs once.
let picksData: PickData[] = []
let gamesData: GameOddsData[] = []

vi.mock('../hooks/useTodaysPicks', () => ({
  useTodaysPicks: () => ({ picks: ok(picksData), record: ok(null), games: ok(gamesData) }),
}))
vi.mock('../hooks/useTopProps', () => ({ useTopProps: () => ok([]) }))
vi.mock('../hooks/useRefreshData', () => ({
  useRefreshData: () => ({ isPending: false, mutate: vi.fn() }),
}))
vi.mock('../components/CreditUsage', () => ({ default: () => null }))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      users: {
        list: vi.fn(),
        create: vi.fn(),
        placePick: vi.fn(),
      },
      paper: {
        quotes: vi.fn(),
        propQuotes: vi.fn(),
      },
    },
  }
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><MemoryRouter initialEntries={['/']}><TodaysPicks /></MemoryRouter></ToastProvider></QueryClientProvider>)
}

function makeGame(overrides: Partial<GameOddsData> = {}): GameOddsData {
  return {
    id: 1, sport: 'mlb', date: '2026-09-29', status: 'scheduled',
    start_time: '2099-01-01T00:00:00Z',
    home_team: 'NYY', away_team: 'BOS', home_team_name: 'New York Yankees', away_team_name: 'Boston Red Sox',
    home_score: null, away_score: null,
    moneyline_home: -150, moneyline_away: 130,
    spread_home: null, over_under: null, bookmaker: null, odds_count: 1,
    last_meeting: null, home_l10_record: '5-5', away_l10_record: '5-5',
    ...overrides,
  }
}

function makePick(overrides: Partial<PickData> = {}): PickData {
  return {
    id: 1, game_id: 1, sport: 'mlb', date: '2026-09-29', pick_type: 'moneyline',
    pick_value: 'NYY ML', stored_pick_value: 'HOME ML',
    confidence: 4, edge_pct: 8.2, odds_at_pick: -150,
    ...overrides,
  }
}

describe('TodaysPicks refresh button', () => {
  beforeEach(() => {
    picksData = []
    gamesData = []
  })
  afterEach(() => setOwnerKey(null))

  it('is hidden from a friend: no owner key in this browser', () => {
    setOwnerKey(null)
    renderPage()
    expect(screen.queryByRole('button', { name: /Refresh data/ })).toBeNull()
  })

  it('is shown to the owner', () => {
    setOwnerKey('owner-key')
    renderPage()
    expect(screen.getByRole('button', { name: /Refresh data/ })).toBeInTheDocument()
  })
})

describe('TodaysPicks -> bet slip wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useSlip.setState({ ...SLIP_DEFAULTS })
  })

  // A moneyline pick's pick_value has already been rewritten to team names
  // ("NYY ML") for display; only stored_pick_value ("HOME ML") maps to a side.
  it('adds a moneyline pick displayed under a team name, at the price the model saw', async () => {
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: 'HOME ML', odds_at_pick: -131 })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, label: 'NYY ML', odds: -131, line: null,
      gameLabel: 'BOS @ NYY', startTime: '2099-01-01T00:00:00Z' })])
  })

  it('adds a fighter-name moneyline pick', async () => {
    picksData = [makePick({ id: 2, pick_value: 'Erick Visconde ML', stored_pick_value: 'AWAY ML' })]
    gamesData = [makeGame({ sport: 'mma', home_team: 'Kleydson Rodrigues', away_team: 'Erick Visconde' })]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'AWAY' }, label: 'Erick Visconde ML' })])
  })

  // The GameCard's button is first in the DOM; the picks-table row's is
  // second -- a separate wiring path with its own stored-label plumbing.
  it('adds a spread pick from the picks-table row, with its line', async () => {
    picksData = [makePick({ pick_type: 'spread', pick_value: 'BOS +3.5', stored_pick_value: 'AWAY +3.5',
      odds_at_pick: -110, home_team: 'NYY', away_team: 'BOS' })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    const betButtons = await screen.findAllByRole('button', { name: 'Bet This' })
    expect(betButtons.length).toBeGreaterThan(1)
    await user.click(betButtons[1])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'BOS +3.5', line: 3.5, odds: -110 })])
  })

  it('cannot add a pick without its stored label (mutation check)', async () => {
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: undefined })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(await screen.findByText("This pick can't be bet here.")).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([])
  })
})
