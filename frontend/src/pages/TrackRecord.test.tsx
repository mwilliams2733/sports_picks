import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TrackRecord from './TrackRecord'
import { api } from '../api/client'
import type { RecordData, CalibrationData } from '../types'

vi.mock('../api/client', () => ({
  api: {
    stats: { emailed: vi.fn(), emailedTrend: vi.fn(), record: vi.fn(), daily: vi.fn(), calibration: vi.fn() },
    picks: { history: vi.fn() },
  },
}))

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[url]}><TrackRecord /></MemoryRouter></QueryClientProvider>)
}

describe('TrackRecord', () => {
  it('renders no Calibration chart and no by-star breakdown while SHOW_STARS is false', async () => {
    vi.mocked(api.stats.record).mockResolvedValue(
      { wins: 3, losses: 2, pushes: 0, total: 5, win_rate: 60, roi: 5, total_profit: 5 } satisfies RecordData
    )
    vi.mocked(api.stats.daily).mockResolvedValue([])
    vi.mocked(api.stats.calibration).mockResolvedValue(
      { tiers: [{ tier: 5, predicted_win_rate: 0.7, actual_win_rate: 0.65, sample_size: 10 }],
       total_graded: 10, brier_score: 0.2 } satisfies CalibrationData
    )
    vi.mocked(api.picks.history).mockResolvedValue([
      { id: 1, game_id: 1, sport: 'nfl', date: '2026-09-20', pick_type: 'moneyline',
        pick_value: 'HOME ML', confidence: 5, edge_pct: 8.0, odds_at_pick: -150,
        matchup: 'BOS @ NYY', result: 'win', payout: 0.67, clv_pct: 1.2 },
    ])
    renderAt('/track-record?source=all')

    expect(await screen.findByText('HOME ML')).toBeInTheDocument()
    expect(screen.queryByText('Calibration')).toBeNull()
    expect(screen.queryByText('Confidence Breakdown')).toBeNull()
    expect(screen.queryByText('Confidence')).toBeNull()
    expect(document.body.textContent).not.toMatch(/[★☆]/)
  })

  it('opens on the emailed record, not the all-picks record', async () => {
    const empty = { label: 'all', wins: 0, losses: 0, pushes: 0, pending: 0, n: 0, win_rate: null, range_low: null,
      range_high: null, break_even: null, profit: 0, staked: 0, roi: null, verdict: null }
    vi.mocked(api.stats.emailed).mockResolvedValue({ kind: 'game', by: 'week', groups: [], total: empty })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [], max_drawdown: 0, longest_losing_streak: 0 })
    vi.mocked(api.stats.record).mockResolvedValue({ wins: 99, losses: 1, pushes: 0, total: 100, win_rate: 99, roi: 50, total_profit: 50 } satisfies RecordData)
    vi.mocked(api.stats.daily).mockResolvedValue([])
    vi.mocked(api.stats.calibration).mockResolvedValue({ tiers: [], total_graded: 0, brier_score: null } satisfies CalibrationData)
    vi.mocked(api.picks.history).mockResolvedValue([])
    renderAt('/track-record')
    expect(await screen.findByText(/No emailed picks have settled yet/)).toBeInTheDocument()
    expect(api.stats.emailed).toHaveBeenCalled()
    expect(screen.queryByText('99-1')).toBeNull()
    expect(screen.getByRole('button', { name: 'Research: all picks' })).toBeInTheDocument()
  })
})
