import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Leaders from './Leaders'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import type { FeedItem, LeaderboardRow, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    users: { ...actual.api.users, list: vi.fn(), leaderboard: vi.fn(), feed: vi.fn() },
    paper: { ...actual.api.paper, quotes: vi.fn(), propQuotes: vi.fn() } } }
})

const row = (over: Partial<LeaderboardRow>): LeaderboardRow => ({ id: 1, name: 'x', is_model: false, wins: 0,
  losses: 0, pushes: 0, pending: 0, n: 0, n_eff: 0, win_rate: null, roi: null, shrunk_roi: null, profit: 0,
  ranked: false, ...over })
const user = (id: number, name: string, streak = 0, type = 'none') =>
  ({ id, name, available_balance: 1000, current_streak: streak, streak_type: type }) as UserProfile
const placed: FeedItem = { id: 9, user_id: 2, event_type: 'pick_placed', created_at: new Date().toISOString(),
  payload: { user_name: 'Sam', message: 'Sam bet AWAY +9 -109 — $100', bet_id: 6, kind: 'straight',
    legs: [{ game_id: 7, pick_type: 'spread', side: 'AWAY', label: 'TB +9', game_label: 'TB @ DAL',
      start_time: '2099-01-01T00:00:00+00:00', home_team: 'DAL', away_team: 'TB', odds: -109, quoted_line: 9 }] } }
const old: FeedItem = { id: 3, user_id: 2, event_type: 'pick_placed', created_at: new Date().toISOString(),
  payload: { user_name: 'Sam', message: 'Sam bet HOME ML -110 — $50' } }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><Leaders /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useSlip.setState({ ...SLIP_DEFAULTS })
  useUserStore.setState({ currentUserName: 'Me' })
  vi.mocked(api.users.list).mockResolvedValue([user(1, 'Me'), user(2, 'Sam', 4, 'win')])
  vi.mocked(api.users.leaderboard).mockResolvedValue([
    row({ id: 2, name: 'Sam', ranked: true, n: 12, wins: 8, losses: 4, profit: 240.5, roi: 0.12 }),
    row({ id: null, name: 'Model', is_model: true, ranked: true, n: 30, wins: 16, losses: 14, profit: 1.2, roi: 0.04 }),
    ...Array.from({ length: 9 }, (_, i) => row({ id: 100 + i, name: `P${i}`, n: 2 })),
    row({ id: 1, name: 'Me', n: 3, wins: 2, losses: 1, profit: 50 }),
  ])
  vi.mocked(api.users.feed).mockResolvedValue([placed, old])
})

describe('Leaders', () => {
  it('ranks the board with streaks, the Model and unranked players (Review Focus 5)', async () => {
    renderPage()
    const sam = await screen.findByRole('row', { name: /Sam/ })
    expect(sam).toHaveTextContent('1')
    expect(sam).toHaveTextContent('+$240.50')
    expect(sam).toHaveTextContent('12.0%')
    expect(sam).toHaveTextContent('8-4')
    expect(sam).toHaveTextContent('🔥 4')
    expect(screen.getAllByRole('row', { name: /Model/ })[0]).not.toHaveTextContent('🔥')
    expect(screen.getAllByRole('row', { name: /Me/ })[0]).toHaveTextContent('Unranked (3/10)')
  })
  it("pins your own row when it is off the top of the board", async () => {
    renderPage()
    expect(await screen.findByLabelText('Your position')).toHaveTextContent('Me')
  })
  it('does not pin your row when it is already on the board', async () => {
    vi.mocked(api.users.leaderboard).mockResolvedValue([row({ id: 1, name: 'Me', n: 3, wins: 2, losses: 1 })])
    renderPage()
    expect(await screen.findByRole('row', { name: /Me/ })).toBeInTheDocument()
    expect(screen.queryByLabelText('Your position')).toBeNull()
  })
  it('links each player to their page and the Model to its track record (Review Focus 4)', async () => {
    renderPage()
    expect(await screen.findByRole('link', { name: 'Sam' })).toHaveAttribute('href', '/players/2')
    expect(screen.getByRole('link', { name: 'Model' })).toHaveAttribute('href', '/track-record')
    expect(screen.getAllByRole('link', { name: 'Me' })[0]).toHaveAttribute('href', '/players/1')
  })
  it('shows the feed, with Tail only on placed bets that carry legs (Review Focus 2)', async () => {
    renderPage()
    expect(await screen.findByText('Sam bet HOME ML -110 — $50')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Tail/ })).toHaveLength(1)
  })
  it('tails a bet onto your slip at the current price', async () => {
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 7, quotes: [{ pick_type: 'spread', side: 'AWAY',
      available: true, pick_value: 'AWAY +8.5', odds: -115, line: 8.5, quoted_at: 'x', prop_player: null,
      prop_market: null }] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Tail/ }))
    await waitFor(() => expect(useSlip.getState().legs.map(l => [l.label, l.odds])).toEqual([['TB +8.5', -115]]))
  })
})
