import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import PaperTrading from './PaperTrading'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      users: {
        list: vi.fn(), leaderboard: vi.fn(), feed: vi.fn(),
        picks: vi.fn(), stats: vi.fn(),
      },
      games: { today: vi.fn() },
      props: { today: vi.fn() },
    },
  }
})

const user = (available_balance: number, roi = 0): UserProfile => ({
  id: 7, name: 'Sam', starting_balance: 10000, current_balance: 10000, available_balance,
  total_wagered: 0, profit: 0, roi, wins: 0, losses: 0, pushes: 0, pending: 0,
  win_rate: 0, current_streak: 0, best_streak: 0, streak_type: 'none',
})

describe('PaperTrading', () => {
  beforeEach(() => {
    vi.mocked(api.users.leaderboard).mockResolvedValue([])
    vi.mocked(api.users.feed).mockResolvedValue([])
    vi.mocked(api.users.picks).mockResolvedValue([])
    vi.mocked(api.users.stats).mockRejectedValue(new Error('not needed'))
    vi.mocked(api.games.today).mockResolvedValue([])
    vi.mocked(api.props.today).mockResolvedValue([])
  })

  it('reads Available from the live users list, not the row clicked earlier', async () => {
    // The store holds the profile as it was when the player was clicked;
    // a bet since then lowered what is available. A placed bet invalidates
    // the users query, so that is where the current number lives.
    useUserStore.setState({ selectedUser: user(10000) })
    vi.mocked(api.users.list).mockResolvedValue([user(9000, 4.5)])
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><ToastProvider><PaperTrading /></ToastProvider></MemoryRouter>
      </QueryClientProvider>)

    const label = await screen.findByText('Available')
    const card = label.closest('.stat-card') as HTMLElement
    await vi.waitFor(() => expect(card).toHaveTextContent('$9.0K'))
    // Every card, not just Available: a settled bet moves ROI too.
    const roi = screen.getByText('ROI').closest('.stat-card') as HTMLElement
    expect(roi).toHaveTextContent('+4.5%')
  })

  it('sends a player to the Lobby to bet instead of its own pick form', async () => {
    useUserStore.setState({ selectedUser: user(10000) })
    vi.mocked(api.users.list).mockResolvedValue([user(10000)])
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><ToastProvider><PaperTrading /></ToastProvider></MemoryRouter>
      </QueryClientProvider>)
    expect(await screen.findByRole('link', { name: 'Lobby' })).toHaveAttribute('href', '/')
    expect(screen.queryByText('Parlay Builder')).not.toBeInTheDocument()
    expect(screen.queryByText('Place a Pick')).not.toBeInTheDocument()
  })
})
