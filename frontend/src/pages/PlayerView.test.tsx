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
    expect(await screen.findByText('Balance, profit and results include parlays; streaks count single bets only, as Leaders does.')).toBeInTheDocument()
  })
})
