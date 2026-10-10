import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PickReasoningPanel from './PickReasoning'
import ModelPicksStrip from './ModelPicksStrip'
import type { BoardGame, GameQuote, PickReasoning } from '../types'

const r: PickReasoning = { model_prob: 0.58, market_prob: 0.52, edge_pct: 10.7, fair_odds: -138, units: 0.87,
  note: 'The model gives Bucs a 58% chance to win.' }

describe('PickReasoningPanel', () => {
  it("shows the model's numbers and its note", () => {
    render(<PickReasoningPanel r={r} />)
    const panel = screen.getByLabelText('Why this pick')
    for (const text of ['58%', '52%', '10.7%', '-138', '0.87u', 'The model gives Bucs a 58% chance to win.'])
      expect(panel).toHaveTextContent(text)
  })
  it('leaves out what it does not have, never NaN or null (Review Focus 4)', () => {
    render(<PickReasoningPanel r={{ ...r, market_prob: null, fair_odds: null, units: null }} />)
    const panel = screen.getByLabelText('Why this pick')
    expect(panel).not.toHaveTextContent('Market')
    expect(panel).not.toHaveTextContent(/fair price/i)
    expect(panel).not.toHaveTextContent(/NaN|null|undefined/)
  })
})

describe('ModelPicksStrip', () => {
  const ml = (side: string, odds: number): GameQuote => ({ pick_type: 'moneyline', side, available: true, odds,
    line: null, pick_value: `${side} ML`, quoted_at: 'x', prop_player: null, prop_market: null } as GameQuote)
  const g: BoardGame = { id: 1, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Bucs',
    away_team: 'Cowboys', prop_count: 0, quotes: [ml('HOME', -150), ml('AWAY', 130)],
    model_pick: { pick_type: 'moneyline', pick_value: 'HOME ML', odds: -150, edge_pct: 10.7, reasoning: r } }

  it('opens the reasoning from "Why?"', () => {
    render(<ModelPicksStrip games={[g]} offline={false} onPick={vi.fn()} />)
    expect(screen.queryByLabelText('Why this pick')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.getByLabelText('Why this pick')).toHaveTextContent('58%')
    expect(screen.getByRole('button', { name: 'Why?' })).toHaveAttribute('aria-expanded', 'true')
  })
  it('has no "Why?" for a pick without reasoning', () => {
    render(<ModelPicksStrip games={[{ ...g, model_pick: { ...g.model_pick!, reasoning: null } }]} offline={false} onPick={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Why?' })).toBeNull()
  })
})
