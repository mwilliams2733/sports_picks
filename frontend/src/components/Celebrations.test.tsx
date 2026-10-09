import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Celebrations from './Celebrations'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { Ticket, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn(), bets: vi.fn() } } }
})

const me = { id: 1, name: 'Marcus', available_balance: 1000 } as UserProfile
const t = (id: number, result: string | null, payout: number | null, kind: Ticket['kind'] = 'straight'): Ticket => ({
  kind, id, stake: 100, odds: -110, to_win: 90.91, result, payout, created_at: '2026-10-08T22:00:00+00:00',
  sgp: false, legs: [],
})
const summary = { available: 1000, balance: 1000, open_stakes: 0, today_pl: 0 }

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Celebrations /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([me])
})

describe('Celebrations', () => {
  it('stays quiet on a first visit and marks past wins seen (Review Focus 2)', async () => {
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(1, 'win', 90.91)] })
    renderIt()
    await waitFor(() => expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-1"]'))
    expect(screen.queryByText(/WINNER/)).not.toBeInTheDocument()
  })

  it('celebrates a win that settled since this device last looked', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(2, 'win', 90.91), t(3, 'loss', -100)] })
    renderIt()
    expect(await screen.findByText('WINNER 🎉 +$90.91')).toBeInTheDocument()
    expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-2","straight-3"]')
  })

  it('never celebrates a loss', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(3, 'loss', -100)] })
    renderIt()
    await waitFor(() => expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-3"]'))
    expect(screen.queryByText(/WINNER/)).not.toBeInTheDocument()
  })

  it('makes a parlay win bigger', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(4, 'win', 264, 'parlay')] })
    renderIt()
    const banner = (await screen.findByText('WINNER 🎉 +$264.00')).closest('.sb-win')
    expect(banner).toHaveClass('sb-win-big')
    expect(screen.getByText('Parlay hit!')).toBeInTheDocument()
  })
})
