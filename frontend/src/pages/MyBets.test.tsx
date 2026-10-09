import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import MyBets from './MyBets'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { Ticket, TicketLeg, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn(), bets: vi.fn() } } }
})

const me = { id: 1, name: 'Marcus', available_balance: 9850 } as UserProfile
const leg = (over: Partial<TicketLeg> = {}): TicketLeg => ({
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null, result: null,
  game: { id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
    status: 'scheduled', home_score: null, away_score: null, live_detail: null }, ...over,
})
const t = (over: Partial<Ticket>): Ticket => ({
  kind: 'straight', id: 1, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg()], ...over,
})
const open = t({ id: 6 })
const sgp = t({ kind: 'parlay', id: 4, odds: 264, to_win: 132, sgp: true,
  legs: [leg({ result: 'win' }), leg({ pick_type: 'over_under', pick_value: 'Over 49' })] })
const won = t({ id: 5, result: 'win', payout: 91.74 })
const lost = t({ id: 3, result: 'loss', payout: -100 })

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><MyBets /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([me])
  vi.mocked(api.users.bets).mockResolvedValue({
    summary: { available: 9850, balance: 10091.74, open_stakes: 150, today_pl: -8.26 },
    tickets: [open, sgp, won, lost],
  })
})

describe('MyBets', () => {
  it('opens on Open bets with the money strip from the server', async () => {
    renderPage()
    expect(await screen.findByRole('tab', { name: 'Open (2)' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$9,850.00')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$10,091.74')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$150.00')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('−$8.26')     // Review Focus 4
    expect(screen.getByRole('article', { name: 'Bet #P-6' })).toHaveTextContent('TB +9')
    expect(screen.getByRole('article', { name: 'Bet #P-6' })).toHaveTextContent('To win $91.74')
    expect(screen.queryByRole('article', { name: 'Bet #P-5' })).not.toBeInTheDocument()
  })

  it('shows a same-game parlay with a dot per leg (Review Focus 3)', async () => {
    renderPage()
    const card = await screen.findByRole('article', { name: 'Bet #PL-4' })
    expect(card).toHaveTextContent('SGP')
    expect(card.querySelectorAll('.sb-dot-won')).toHaveLength(1)
    expect(card.querySelectorAll('.sb-dot-pending')).toHaveLength(1)
  })

  it('lists settled bets with their result, filterable', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: 'Settled' }))
    const wonCard = screen.getByRole('article', { name: 'Bet #P-5' })
    expect(wonCard).toHaveTextContent('Won +$91.74')
    expect(wonCard).toHaveClass('sb-bet-won')
    expect(screen.getByRole('article', { name: 'Bet #P-3' })).toHaveTextContent('Lost −$100.00')
    fireEvent.click(screen.getByRole('button', { name: 'Won' }))
    expect(screen.queryByRole('article', { name: 'Bet #P-3' })).not.toBeInTheDocument()
  })

  it('says so when there is no player, or no bets (Review Focus 1)', async () => {
    useUserStore.setState({ currentUserName: null })
    const { unmount } = renderPage()
    expect(await screen.findByText(/Choose a player in the top bar/)).toBeInTheDocument()
    unmount()
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 10000, balance: 10000, open_stakes: 0, today_pl: 0 }, tickets: [] })
    renderPage()
    expect(await screen.findByText(/No open bets/)).toBeInTheDocument()
  })

  it('marks a live ticket and tints its legs', async () => {
    const liveGame = { id: 9, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
      status: 'in_progress', home_score: 21, away_score: 14, live_detail: 'Q3 4:12' }
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 9900, balance: 10000, open_stakes: 100, today_pl: 0 },
      tickets: [t({ id: 8, legs: [leg({ game: liveGame }),
        leg({ pick_type: 'prop', pick_value: 'Dak Prescott Over 255.5 Pass Yards', game: liveGame })] })] })
    renderPage()
    const card = await screen.findByRole('article', { name: 'Bet #P-8' })
    expect(card).toHaveTextContent('LIVE')
    expect(card).toHaveTextContent('TB 14 – DAL 21 · Q3 4:12')
    expect(card).toHaveTextContent('Winning')                     // TB +9, down 7
    expect(card.querySelectorAll('.sb-tint')).toHaveLength(1)     // the prop gets none
  })
})
