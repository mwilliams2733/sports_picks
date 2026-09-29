import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import EmailedRecord from './EmailedRecord'
import { api } from '../api/client'
import type { EmailedSummary } from '../types'

vi.mock('../api/client', () => ({ api: { stats: { emailed: vi.fn(), emailedTrend: vi.fn() } } }))

const s = (o: Partial<EmailedSummary>): EmailedSummary => ({
  label: 'all', wins: 0, losses: 0, pushes: 0, pending: 0, n: 0, win_rate: null,
  range_low: null, range_high: null, break_even: null, profit: 0, staked: 0,
  roi: null, verdict: null, ...o,
})

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><EmailedRecord /></MemoryRouter></QueryClientProvider>)
}

describe('EmailedRecord', () => {
  it('shows an empty state before anything has settled, never NaN', async () => {
    vi.mocked(api.stats.emailed).mockResolvedValue({ kind: 'game', by: 'week', groups: [], total: s({ pending: 5 }) })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [], max_drawdown: 0, longest_losing_streak: 0 })
    renderIt()
    expect(await screen.findByText(/No emailed picks have settled yet/)).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).toBeNull()
  })

  it('shows win rate beside break-even and colours a clear verdict', async () => {
    vi.mocked(api.stats.emailed).mockResolvedValue({
      kind: 'game', by: 'week',
      groups: [s({ label: '2026-09-28', wins: 40, losses: 5, n: 45, win_rate: 0.889, range_low: 0.79,
                   range_high: 0.94, break_even: 0.524, roi: 0.7, verdict: 'above' })],
      total: s({ wins: 40, losses: 5, n: 45, win_rate: 0.889, break_even: 0.524, roi: 0.7, verdict: 'above' }),
    })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [{ date: '2026-09-28', units: 31.4 }], max_drawdown: 2, longest_losing_streak: 1 })
    renderIt()
    const cell = await screen.findByTestId('winrate-2026-09-28')
    expect(cell).toHaveTextContent('88.9%')
    expect(cell.className).toContain('verdict-above')
    expect(screen.getByTestId('breakeven-2026-09-28')).toHaveTextContent('52.4%')
  })

  it('offers no Stars grouping while stars are hidden', async () => {
    vi.mocked(api.stats.emailed).mockResolvedValue({ kind: 'game', by: 'week', groups: [], total: s({}) })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [], max_drawdown: 0, longest_losing_streak: 0 })
    renderIt()
    await screen.findByText(/No emailed picks have settled yet/)
    expect(screen.queryByRole('button', { name: 'Stars' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Week' })).toBeInTheDocument()
  })
})
