import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import QuotePicker from './QuotePicker'
import type { GameQuote } from '../types'

const quotes: GameQuote[] = [
  { pick_type: 'spread', side: 'HOME', available: true, pick_value: 'HOME -3.5', odds: -112, line: -3.5,
    quoted_at: '2026-10-04T14:00:00+00:00', prop_player: null, prop_market: null },
  { pick_type: 'spread', side: 'AWAY', available: false, reason: 'stale',
    message: 'The price is stale — ask Marcus to refresh.' },
]

describe('QuotePicker', () => {
  it('shows each side with its line and price', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Chiefs -3.5\s+-112/ })).toBeEnabled()
  })

  it('disables an unavailable side and shows why', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Bills/ })).toBeDisabled()
    expect(screen.getByText('The price is stale — ask Marcus to refresh.')).toBeInTheDocument()
  })

  it('reports the chosen quote', async () => {
    const onSelect = vi.fn()
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={onSelect} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Chiefs/ }))
    expect(onSelect).toHaveBeenCalledWith(quotes[0])
  })

  it('has no price input', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.queryByRole('spinbutton')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
