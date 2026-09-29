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
