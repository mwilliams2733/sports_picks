import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Backtesting from './Backtesting'
import { api } from '../api/client'
import type { RunAllResult } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      stats: { daily: vi.fn() },
      backtest: { runAll: vi.fn(), run: vi.fn(), autoTune: vi.fn() },
      pipeline: { run: vi.fn() },
    },
    getErrorMessage: actual.getErrorMessage,
  }
})

const RESULT: RunAllResult = {
  sport: 'nba', start_date: '2026-09-01', end_date: '2026-09-10', games_count: 40,
  sport_markets: [],
  variants: {
    ensemble: { wins: 20, losses: 15, total: 35, win_rate: 57, roi: 4, total_profit: 4 },
    prop_value: {
      wins: 12, losses: 8, total: 20, hit_rate: 60, roi: 3, total_profit: 3,
      by_confidence: { 5: { wins: 5, losses: 1 }, 3: { wins: 4, losses: 3 } },
    },
  },
}

describe('Backtesting', () => {
  it('shows no "By Confidence" star breakdown for the Player Props variant while SHOW_STARS is false', async () => {
    const user = userEvent.setup()
    vi.mocked(api.stats.daily).mockResolvedValue([])
    vi.mocked(api.backtest.runAll).mockResolvedValue(RESULT)

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><Backtesting /></QueryClientProvider>)
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    await user.click(screen.getByRole('button', { name: /Run All Variants/ }))
    await screen.findByText('Player Props')
    await user.click(screen.getByText('Player Props'))

    expect(screen.queryByText('By Confidence')).toBeNull()
    expect(document.body.textContent).not.toMatch(/[★☆]|\*{3,}/)
  })
})
