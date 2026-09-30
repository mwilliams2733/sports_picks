import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import GameCard from './GameCard'
import type { GameOddsData } from '../types'

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
})
