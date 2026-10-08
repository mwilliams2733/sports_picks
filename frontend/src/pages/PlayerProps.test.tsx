import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '../api/client'
import PlayerProps from './PlayerProps'
import type { PropData } from '../types'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'

const ok = <T,>(data: T) => ({ data, error: null, isLoading: false })

const LOW_CONFIDENCE_PROP: PropData = {
  id: 1, game_id: 1, sport: 'nba', date: '2026-09-29', matchup: 'BOS @ NYY',
  bookmaker: 'draftkings', market: 'player_points', market_label: 'Points',
  player_name: 'Jayson Tatum', outcome: 'Over', line: 27.5, odds: -110,
  projection: 29.0, edge_pct: 6.0, confidence: 1, season_avg: 28.0,
  recent_avg: 29.5, source: 'season average', is_stale: false,
}

vi.mock('../hooks/useProps', () => ({
  useProps: () => ({ props: ok([LOW_CONFIDENCE_PROP]), markets: ok([]) }),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, paper: { ...actual.api.paper, board: vi.fn() } } }
})

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[url]}><PlayerProps /></MemoryRouter></QueryClientProvider>)
}

describe('PlayerProps', () => {
  it('ignores ?confidence=3 while SHOW_STARS is false, so a shared link cannot silently hide props', () => {
    renderAt('/player-props?confidence=3')
    // LOW_CONFIDENCE_PROP has confidence=1; a minConfidence of 3 would hide
    // it if the URL param were honored.
    expect(screen.getByText('Jayson Tatum')).toBeInTheDocument()
    expect(screen.queryByText(/\+ Stars/)).toBeNull()
    expect(document.body.textContent).not.toMatch(/[★☆]/)
  })

  it("adds a prop to the bet slip at the model's price", () => {
    useSlip.setState({ ...SLIP_DEFAULTS })
    renderAt('/player-props')
    fireEvent.click(screen.getByRole('button', { name: 'Bet This' }))
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'prop', prop_player: 'Jayson Tatum', prop_market: 'player_points', outcome: 'Over', line: 27.5 },
      label: 'Jayson Tatum Over 27.5 Points', gameLabel: 'BOS @ NYY', odds: -110 })])
  })

  it('gives a prop leg its game start from the board, so it can close on time (final review)', async () => {
    useSlip.setState({ ...SLIP_DEFAULTS })
    vi.mocked(api.paper.board).mockResolvedValue({ games: [{ id: 1, sport: 'nba', date: '2026-09-29',
      start_time: '2026-09-29T23:30:00+00:00', home_team: 'NYY', away_team: 'BOS', quotes: [], prop_count: null,
      model_pick: null }] })
    renderAt('/player-props')
    // add() is idempotent (one leg per market), so clicking until the board
    // has loaded is safe.
    await vi.waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Bet This' }))
      expect(useSlip.getState().legs[0]?.startTime).toBe('2026-09-29T23:30:00+00:00')
    })
    expect(useSlip.getState().legs).toHaveLength(1)
  })
})
