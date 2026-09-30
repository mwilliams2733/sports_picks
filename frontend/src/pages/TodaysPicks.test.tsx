import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TodaysPicks from './TodaysPicks'
import { setOwnerKey } from '../lib/secrets'
import { useUserStore } from '../stores/userStore'
import { api } from '../api/client'
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
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={['/']}><TodaysPicks /></MemoryRouter></QueryClientProvider>)
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

describe('TodaysPicks -> GameCard -> BetModal wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUserStore.setState({ currentUserName: 'Marcus', selectedUser: null })
    window.localStorage.clear()
    window.sessionStorage.clear()
    vi.mocked(api.users.list).mockResolvedValue([
      { id: 1, name: 'Marcus', starting_balance: 10000, current_balance: 10000,
        total_wagered: 0, profit: 0, roi: 0, wins: 0, losses: 0, pushes: 0,
        pending: 0, win_rate: 0, current_streak: 0, best_streak: 0, streak_type: 'none' },
    ])
    vi.mocked(api.paper.propQuotes).mockResolvedValue({ game_id: 1, quotes: [] })
  })

  // A moneyline pick's pick_value has already been rewritten to team names
  // ("NYY ML") for display; only stored_pick_value ("HOME ML") is a side
  // quotes.legFromPick can map. This renders the real GameCard -> BetModal
  // wiring TodaysPicks uses, not legFromPick in isolation.
  it('bets a moneyline pick displayed under a team name', async () => {
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: 'HOME ML' })]
    gamesData = [makeGame()]
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -131,
        line: null, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })

    const user = userEvent.setup()
    renderPage()

    const betButtons = await screen.findAllByRole('button', { name: 'Bet This' })
    await user.click(betButtons[0])
    expect(await screen.findByText('-131')).toBeInTheDocument()

    await user.type(await screen.findByLabelText('PIN'), '1234')
    expect(screen.getByRole('button', { name: /Confirm/ })).not.toBeDisabled()
  })

  // A fighter-name label (MMA) is likewise a rewrite of an AWAY ML pick.
  it('bets a fighter-name moneyline pick', async () => {
    picksData = [makePick({
      id: 2, pick_value: 'Erick Visconde ML', stored_pick_value: 'AWAY ML',
    })]
    gamesData = [makeGame({ sport: 'mma', home_team: 'Kleydson Rodrigues', away_team: 'Erick Visconde' })]
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'AWAY', available: true, pick_value: 'AWAY ML', odds: -131,
        line: null, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })

    const user = userEvent.setup()
    renderPage()

    const betButtons = await screen.findAllByRole('button', { name: 'Bet This' })
    await user.click(betButtons[0])
    expect(await screen.findByText('-131')).toBeInTheDocument()

    await user.type(await screen.findByLabelText('PIN'), '1234')
    await waitFor(() => expect(screen.getByRole('button', { name: /Confirm/ })).not.toBeDisabled())
  })

  it('cannot bet without the stored label (mutation check): dropping betValue disables Confirm', async () => {
    // This mirrors what happens if the betValue pass-through were removed:
    // BetModal falls back to the display pickValue, which legFromPick can't
    // map, so the pick can't be bet.
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: undefined })]
    gamesData = [makeGame()]
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -131,
        line: null, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })

    const user = userEvent.setup()
    renderPage()

    const betButtons = await screen.findAllByRole('button', { name: 'Bet This' })
    await user.click(betButtons[0])
    expect(await screen.findByText("This pick can't be bet here.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm/ })).toBeDisabled()
  })
})
