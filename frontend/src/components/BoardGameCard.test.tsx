import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BoardGameCard from './BoardGameCard'
import ModelPicksStrip from './ModelPicksStrip'
import type { BoardGame, GameQuote } from '../types'

const ok = (pick_type: string, side: string, odds: number, line: number | null, pick_value: string): GameQuote => ({
  pick_type, side, available: true, odds, line, pick_value, quoted_at: 'x', prop_player: null, prop_market: null,
} as GameQuote)
const game: BoardGame = {
  id: 7, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Buccaneers', away_team: 'Cowboys',
  prop_count: 42, model_pick: { pick_type: 'spread', pick_value: 'AWAY +3', odds: -110, edge_pct: 4.1 },
  quotes: [
    ok('spread', 'HOME', -110, -3, 'HOME -3'), ok('spread', 'AWAY', -110, 3, 'AWAY +3'),
    ok('moneyline', 'HOME', -160, null, 'HOME ML'), ok('moneyline', 'AWAY', 135, null, 'AWAY ML'),
    ok('over_under', 'Over', -110, 47.5, 'Over 47.5'),
    { pick_type: 'over_under', side: 'Under', available: false, reason: 'stale', message: 'stale!' },
  ],
}

describe('BoardGameCard', () => {
  it('puts the away team first with its spread, moneyline and Over tiles', () => {
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={() => {}} /></MemoryRouter>)
    const rows = screen.getAllByTestId('team-row')
    expect(rows[0]).toHaveTextContent('Cowboys')
    expect(rows[0]).toHaveTextContent('+3')
    expect(rows[0]).toHaveTextContent('+135')
    expect(rows[0]).toHaveTextContent('O 47.5')
    expect(rows[1]).toHaveTextContent('Buccaneers')
    expect(screen.getByText('TBD')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '+42 props ›' })).toHaveAttribute('href', '/game/7')
  })
  it('locks a refused side and still lists the game', () => {
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: /Buccaneers Under locked/ })).toBeDisabled()
  })
  it('hands the tapped side to onPick as a slip selection', () => {
    const onPick = vi.fn()
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={onPick} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: /Cowboys ML \+135/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'moneyline', side: 'AWAY' }, label: 'Cowboys ML', odds: 135 }))
  })
  it('shows "Props ›" with no count when the board sends none', () => {
    render(<MemoryRouter><BoardGameCard game={{ ...game, prop_count: null }} offline={false} onPick={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Props ›' })).toBeInTheDocument()
  })
})

describe('ModelPicksStrip', () => {
  it('shows a card per mappable model pick with its edge, tappable', () => {
    const onPick = vi.fn()
    render(<ModelPicksStrip games={[game]} offline={false} onPick={onPick} />)
    expect(screen.getByText('Cowboys +3')).toBeInTheDocument()
    expect(screen.getByText(/Model edge 4.1%/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Cowboys \+3 -110/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'spread', side: 'AWAY' }, odds: -110, line: 3 }))
  })
  it('shows the price the edge was computed at and bets at it, not at the live price', () => {
    const onPick = vi.fn()
    const g = { ...game, model_pick: { pick_type: 'moneyline' as const, pick_value: 'AWAY ML', odds: 150, edge_pct: 4.1 } }
    render(<ModelPicksStrip games={[g]} offline={false} onPick={onPick} />)   // live price +135
    expect(screen.getByText(/Model edge 4.1% at \+150/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /\+135/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'moneyline', side: 'AWAY' }, odds: 150 }))
  })
  it('renders nothing when no game has a usable model pick', () => {
    const { container } = render(<ModelPicksStrip games={[{ ...game, model_pick: null }]} offline={false} onPick={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
  it('shows a locked chip when the picked side is refused', () => {
    const g = { ...game, model_pick: { pick_type: 'over_under' as const, pick_value: 'Under 47.5', odds: -110, edge_pct: 3 } }
    render(<ModelPicksStrip games={[g]} offline={false} onPick={() => {}} />)
    expect(screen.getByRole('button', { name: /locked/ })).toBeDisabled()
  })
})
