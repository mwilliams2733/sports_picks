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
  it("shows the bettor's note as plain text (Review Focus 2)", () => {
    renderList({ tickets: [t({ id: 7, note: '<b>Backup QB</b> starts. Confidence 2/5 · Model: no side.' })], emptyOpen: 'none' })
    const card = screen.getByRole('article', { name: 'Bet #P-7' })
    expect(card).toHaveTextContent('<b>Backup QB</b> starts. Confidence 2/5 · Model: no side.')
    expect(card.querySelector('b')).toBeNull()
  })
  it('shows the loading text, not an empty message, before tickets arrive', () => {
    renderList({ tickets: undefined, loading: true, loadingText: 'Loading your bets…', emptyOpen: 'none' })
    expect(screen.getByText('Loading your bets…')).toBeInTheDocument()
    expect(screen.queryByText('none')).toBeNull()
  })
})
