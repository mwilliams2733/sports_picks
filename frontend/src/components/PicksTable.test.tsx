import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PicksTable from './PicksTable'
import type { PickData } from '../types'

function makePick(overrides: Partial<PickData> = {}): PickData {
  return {
    id: 1,
    game_id: 100,
    sport: 'nba',
    date: '2026-07-10',
    pick_type: 'moneyline',
    pick_value: 'HOME ML',
    confidence: 4,
    edge_pct: 12.5,
    odds_at_pick: -150,
    matchup: 'LAL @ BOS',
    ...overrides,
  }
}

describe('PicksTable', () => {
  it('shows an empty state when there are no picks', () => {
    render(<PicksTable picks={[]} />)
    expect(screen.getByText('No picks available')).toBeInTheDocument()
  })

  it('renders a row per pick with matchup, pick value, and odds', () => {
    render(<PicksTable picks={[makePick()]} />)
    expect(screen.getByText('LAL @ BOS')).toBeInTheDocument()
    expect(screen.getByText('HOME ML')).toBeInTheDocument()
    expect(screen.getByText('-150')).toBeInTheDocument()
  })

  it('shows win/loss/push result badges when showResult is set', () => {
    render(
      <PicksTable
        picks={[
          makePick({ id: 1, result: 'win', payout: 0.67 }),
          makePick({ id: 2, result: 'loss', payout: -1 }),
          makePick({ id: 3, result: 'push', payout: 0 }),
          makePick({ id: 4, result: null }),
        ]}
        showResult
      />
    )
    expect(screen.getByText(/WIN/)).toBeInTheDocument()
    expect(screen.getByText(/LOSS/)).toBeInTheDocument()
    expect(screen.getByText(/PUSH/)).toBeInTheDocument()
    expect(screen.getByText('Pending')).toBeInTheDocument()
  })

  it('calls onBet with the clicked pick', async () => {
    const user = userEvent.setup()
    const onBet = vi.fn()
    const pick = makePick()
    render(<PicksTable picks={[pick]} onBet={onBet} />)

    await user.click(screen.getByRole('button', { name: 'Bet This' }))
    expect(onBet).toHaveBeenCalledWith(pick)
  })
})
