import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import GameCard from './GameCard'
import type { GameOddsData, PickData } from '../types'

function makeGame(overrides: Partial<GameOddsData> = {}): GameOddsData {
  return {
    id: 1, sport: 'nba', date: '2026-09-29', status: 'scheduled',
    start_time: '2099-01-01T00:00:00Z',
    home_team: 'NYY', away_team: 'BOS', home_team_name: 'New York Yankees', away_team_name: 'Boston Red Sox',
    home_score: null, away_score: null,
    moneyline_home: -150, moneyline_away: 130,
    spread_home: -3.5, over_under: 220.5, bookmaker: 'consensus', odds_count: 1,
    last_meeting: null, home_l10_record: '5-5', away_l10_record: '5-5',
    ...overrides,
  }
}

function makePick(overrides: Partial<PickData> = {}): PickData {
  return {
    id: 1, game_id: 1, sport: 'nba', date: '2026-09-29', pick_type: 'moneyline',
    pick_value: 'HOME ML', confidence: 5, edge_pct: 8.0, odds_at_pick: -150,
    matchup: 'BOS @ NYY',
    ...overrides,
  }
}

describe('GameCard', () => {
  // /games/today now derives its prices from pricing.game_quotes (plan 027
  // final fix, FIX B) -- a refused market comes back null, not a stale
  // average. GameCard must render that without crashing.
  it('renders a dash instead of crashing when every price is null (all books stale)', () => {
    const game = makeGame({ moneyline_home: null, moneyline_away: null, spread_home: null, over_under: null, bookmaker: null })
    render(<GameCard game={game} picks={[]} onBet={vi.fn()} />)
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/Spread:/)).toBeNull()
    expect(screen.queryByText(/O\/U:/)).toBeNull()
  })

  it('renders no confidence stars for the top pick (owner decision 2026-09-28)', () => {
    const game = makeGame()
    const { container } = render(
      <GameCard game={game} picks={[makePick({ game_id: game.id })]} onBet={vi.fn()} />
    )
    expect(container.textContent).not.toMatch(/[\u2605\u2606]/)
  })
})
