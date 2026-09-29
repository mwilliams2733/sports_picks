import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LeaderboardBar from './LeaderboardBar'
import type { LeaderboardRow } from '../types'

const row = (o: Partial<LeaderboardRow>): LeaderboardRow => ({
  id: 1, name: 'x', is_model: false, wins: 0, losses: 0, pushes: 0, pending: 0,
  n: 0, n_eff: 0, win_rate: null, roi: null, shrunk_roi: null, profit: 0, ranked: false, ...o,
})

describe('LeaderboardBar', () => {
  it('numbers only ranked rows and marks the rest', () => {
    render(<LeaderboardBar selectedId={null} onSelect={vi.fn()} rows={[
      row({ id: 1, name: 'Amy', ranked: true, n: 12, roi: 0.336, shrunk_roi: 0.051, win_rate: 0.58 }),
      row({ id: null, name: 'Model', is_model: true, ranked: true, n: 20, roi: 0.02, shrunk_roi: -0.02, win_rate: 0.55 }),
      row({ id: 3, name: 'Bo', ranked: false, n: 4 }),
    ]} />)
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('needs 10 bets')).toBeInTheDocument()
    expect(screen.getByText('+5.1%')).toBeInTheDocument()        // adjusted ROI, the rank metric
    expect(screen.getByText('raw +33.6%')).toBeInTheDocument()   // raw ROI beside it
  })

  it('selects players but not the model', async () => {
    const onSelect = vi.fn()
    render(<LeaderboardBar selectedId={null} onSelect={onSelect} rows={[
      row({ id: 7, name: 'Amy', ranked: true, n: 10, roi: 0.1 }),
      row({ id: null, name: 'Model', is_model: true, ranked: true, n: 10, roi: 0.0 }),
    ]} />)
    await userEvent.click(screen.getByText('Amy'))
    await userEvent.click(screen.getByText('Model'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(7)
  })

  it('shows a dash, never NaN, with no settled bets', () => {
    render(<LeaderboardBar selectedId={null} onSelect={vi.fn()} rows={[row({ name: 'New' })]} />)
    expect(screen.queryByText(/NaN/)).toBeNull()
    expect(screen.getAllByText('—').length).toBe(2)    // adjusted ROI and win rate
    expect(screen.getByText('raw —')).toBeInTheDocument()
  })

  it('renders the ranking caption outside the scrollable bar', () => {
    render(<LeaderboardBar selectedId={null} onSelect={vi.fn()} rows={[]} />)
    const caption = screen.getByText('Ranked by adjusted ROI · single bets only · 10 settled bets to be ranked')
    expect(caption).toBeInTheDocument()
    expect(caption.closest('.leaderboard-bar')).toBeNull()
  })
})
