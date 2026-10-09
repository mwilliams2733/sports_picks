import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PeriodStatsTable from './PeriodStatsTable'
import type { PeriodStats, UserStats } from '../types'

const p = (over: Partial<PeriodStats> = {}): PeriodStats => ({
  wins: 0, losses: 0, pushes: 0, cashed_out: 0, total: 0, win_rate: 0, profit: 0, roi: 0, ...over })
const stats = (over: Partial<UserStats> = {}): UserStats => ({
  today: p(), this_week: p(), this_month: p(), all_time: p(), daily_breakdown: [], ...over })
const cells = (name: string) =>
  Array.from(screen.getByRole('row', { name }).querySelectorAll('td')).map(td => td.textContent)

describe('PeriodStatsTable', () => {
  it("shows each period's record, win rate, profit and ROI", () => {
    render(<PeriodStatsTable stats={stats({
      this_week: p({ wins: 3, losses: 1, pushes: 1, total: 5, win_rate: 75, profit: 182.4, roi: 36.48 }),
      all_time: p({ wins: 8, losses: 9, total: 17, win_rate: 47.1, profit: -120.5, roi: -7.09 }) })} />)
    expect(cells('This week')).toEqual(['This week', '3-1-1', '75%', '+$182.40', '+36.48%'])
    expect(cells('All time')).toEqual(['All time', '8-9', '47.1%', '−$120.50', '−7.09%'])
  })
  it('shows dashes for a period with no settled bets, and for win rate with nothing decided (Review Focus 3)', () => {
    render(<PeriodStatsTable stats={stats({ this_month: p({ pushes: 2, total: 2 }) })} />)
    expect(cells('Today')).toEqual(['Today', '—', '—', '—', '—'])
    expect(cells('This month')).toEqual(['This month', '0-0-2', '—', '$0.00', '0%'])
  })
  it('notes cashed-out bets only when there are some', () => {
    const { unmount } = render(<PeriodStatsTable stats={stats({
      all_time: p({ wins: 1, cashed_out: 2, total: 3, win_rate: 100, profit: 50, roi: 10 }) })} />)
    expect(screen.getByText('Profit and ROI include 2 cashed-out bets; W-L does not.')).toBeInTheDocument()
    unmount()
    render(<PeriodStatsTable stats={stats({ all_time: p({ wins: 1, cashed_out: 1, total: 2, win_rate: 100 }) })} />)
    expect(screen.getByText('Profit and ROI include 1 cashed-out bet; W-L does not.')).toBeInTheDocument()
  })
  it('has no note without cash outs', () => {
    render(<PeriodStatsTable stats={stats({ all_time: p({ wins: 1, total: 1, win_rate: 100 }) })} />)
    expect(screen.queryByText(/cashed-out/)).toBeNull()
  })
})
