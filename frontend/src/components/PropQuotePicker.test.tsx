import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropQuotePicker from './PropQuotePicker'
import type { PropQuote } from '../types'

const base = { prop_market: 'player_pass_yds', market_label: 'Pass Yards', line: 225.5,
  quoted_at: '2026-10-04T14:00:00+00:00', pick_type: 'prop' } as const
const quotes: PropQuote[] = [
  { ...base, prop_player: 'Jalen Hurts', outcome: 'Over', available: true,
    pick_value: 'Jalen Hurts Over 225.5 Pass Yards', odds: -115 },
  { ...base, prop_player: 'Jalen Hurts', outcome: 'Under', available: true,
    pick_value: 'Jalen Hurts Under 225.5 Pass Yards', odds: -105 },
  { ...base, prop_player: 'Josh Allen', outcome: 'Over', available: false,
    reason: 'stale', message: 'The price is stale — ask Marcus to refresh.' } as PropQuote,
]

describe('PropQuotePicker', () => {
  it('shows one row per player/market/line with Over and Under prices', () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Over 225.5\s+-115/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Under 225.5\s+-105/ })).toBeEnabled()
  })

  it('filters by the search box', async () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    await userEvent.setup().type(screen.getByLabelText('Search props'), 'allen')
    expect(screen.queryByText('Jalen Hurts')).toBeNull()
    expect(screen.getByText('Josh Allen')).toBeInTheDocument()
  })

  it('disables an unavailable side', () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    const row = screen.getByText('Josh Allen').closest('.prop-quote-row') as HTMLElement
    expect(row.querySelector('button')).toBeDisabled()
  })

  it('reports the chosen quote', async () => {
    const onSelect = vi.fn()
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={onSelect} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Under 225.5/ }))
    expect(onSelect).toHaveBeenCalledWith(quotes[1])
  })
})
