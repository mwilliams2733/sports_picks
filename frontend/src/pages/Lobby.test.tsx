import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Lobby from './Lobby'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import type { BoardGame, GameQuote } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    paper: { board: vi.fn(), quotes: vi.fn(), propQuotes: vi.fn() },
    users: { ...actual.api.users, list: vi.fn(), feed: vi.fn() } } }
})

const ml = (side: string, odds: number): GameQuote => ({ pick_type: 'moneyline', side, available: true, odds,
  line: null, pick_value: `${side} ML`, quoted_at: 'x', prop_player: null, prop_market: null } as GameQuote)
const g = (id: number, sport: string, date: string): BoardGame => ({
  id, sport, date, start_time: null, home_team: `H${id}`, away_team: `A${id}`, prop_count: 0, model_pick: null,
  quotes: [ml('HOME', -150), ml('AWAY', 130)],
})

function renderLobby(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}><ToastProvider><MemoryRouter><Lobby /></MemoryRouter></ToastProvider></QueryClientProvider>)
}

describe('Lobby', () => {
  beforeEach(() => {
    useSlip.setState({ ...SLIP_DEFAULTS })
    vi.mocked(api.users.list).mockResolvedValue([])
    vi.mocked(api.users.feed).mockResolvedValue([])
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [] })
  })

  it('shows sport tabs in board order and the first sport by default', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nba', '2026-10-20'), g(2, 'nfl', '2026-10-21')] })
    renderLobby()
    expect(await screen.findByRole('tab', { name: 'NBA' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.queryByText('A2')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'NFL' }))
    expect(screen.getByText('A2')).toBeInTheDocument()
  })

  it('says so when the board is empty', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [] })
    renderLobby()
    expect(await screen.findByText(/No games on the board/)).toBeInTheDocument()
  })

  it('adds a tapped price to the bet slip and marks it selected', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nfl', '2026-10-20')] })
    renderLobby()
    const tile = await screen.findByRole('button', { name: /A1 ML \+130/ })
    fireEvent.click(tile)
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'AWAY' }, label: 'A1 ML', odds: 130 })])
    expect(tile).toHaveAttribute('aria-pressed', 'true')
  })

  it('locks every tile and shows the banner when a refetch fails over old data', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['paper', 'board'], { games: [g(1, 'nfl', '2026-10-20')] })
    vi.mocked(api.paper.board).mockRejectedValue(new Error('offline'))
    renderLobby(client)
    expect(await screen.findByRole('alert')).toHaveTextContent('Board offline — prices unavailable')
    expect(screen.getByText('A1')).toBeInTheDocument()            // old games still shown
    await waitFor(() => {
      for (const b of screen.getAllByRole('button', { name: /locked/ })) expect(b).toBeDisabled()
    })
    expect(screen.queryByRole('button', { name: /\+130/ })).not.toBeInTheDocument()
  })

  it('runs a one-line ticker of the latest league activity', async () => {
    vi.mocked(api.users.feed).mockResolvedValue([{ id: 1, user_id: 2, event_type: 'pick_won',
      created_at: new Date().toISOString(), payload: { message: 'Sam won TB +9 — +$91.74' } }])
    vi.mocked(api.paper.board).mockResolvedValue({ games: [] })
    renderLobby()
    expect(await screen.findByRole('link', { name: /Sam won TB \+9/ })).toHaveAttribute('href', '/leaders')
  })
})
