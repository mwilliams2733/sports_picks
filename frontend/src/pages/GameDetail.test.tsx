import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import GameDetail from './GameDetail'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import type { BoardGame, PropQuote } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    paper: { board: vi.fn(), quotes: vi.fn(), propQuotes: vi.fn() },
    users: { ...actual.api.users, list: vi.fn() } } }
})

const game: BoardGame = { id: 5, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Bucs',
  away_team: 'Cowboys', prop_count: 1, model_pick: null, quotes: [] }
const prop: PropQuote = { available: true, pick_type: 'prop', pick_value: 'QB One Over 245.5 Pass Yds', odds: -115,
  quoted_at: 'x', prop_player: 'QB One', prop_market: 'player_pass_yds', market_label: 'Pass Yds', outcome: 'Over', line: 245.5 }

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ToastProvider><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/game/:id" element={<GameDetail />} /></Routes></MemoryRouter></ToastProvider></QueryClientProvider>)
}

describe('GameDetail', () => {
  beforeEach(() => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [game] })
    vi.mocked(api.paper.propQuotes).mockResolvedValue({ game_id: 5, quotes: [prop] })
    vi.mocked(api.users.list).mockResolvedValue([])
  })

  it('shows the matchup and every locked line when nothing is priced', async () => {
    renderAt('/game/5')
    expect(await screen.findByRole('heading', { name: 'Cowboys @ Bucs' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /locked/ })).toHaveLength(6)
  })

  it('lists props by market with Over and Under tiles', async () => {
    renderAt('/game/5')
    fireEvent.click(await screen.findByRole('tab', { name: 'Player Props' }))
    expect(await screen.findByRole('heading', { name: 'Pass Yds' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /QB One Over 245.5 -115/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /QB One Under 245.5 locked/ })).toBeDisabled()
  })

  it('says the game is off the board when it is not on it', async () => {
    renderAt('/game/999')
    expect(await screen.findByText(/off the board/)).toBeInTheDocument()
  })
})
