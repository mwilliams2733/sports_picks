import { describe, it, expect } from 'vitest'
import { formatOdds, legFromPick, priceMoveNote, parlayEstimate, ageLabel, findQuote } from './quotes'
import type { GameQuote, PropQuote } from '../types'

describe('legFromPick', () => {
  it('maps a moneyline label to a side', () => {
    expect(legFromPick('moneyline', 'HOME ML', 3)).toEqual({ game_id: 3, pick_type: 'moneyline', side: 'HOME' })
    expect(legFromPick('moneyline', 'AWAY ML', 3)).toEqual({ game_id: 3, pick_type: 'moneyline', side: 'AWAY' })
  })
  it('maps a spread label to a side and ignores its number', () => {
    expect(legFromPick('spread', 'AWAY +3.5', 3)).toEqual({ game_id: 3, pick_type: 'spread', side: 'AWAY' })
  })
  it('maps a total label to Over or Under', () => {
    expect(legFromPick('over_under', 'Over 220.5', 3)).toEqual({ game_id: 3, pick_type: 'over_under', side: 'Over' })
  })
  it('maps a prop label to its prop fields', () => {
    expect(legFromPick('prop', 'Jalen Hurts Over 225.5', 3, 'player_pass_yds', 'Jalen Hurts')).toEqual({
      game_id: 3, pick_type: 'prop', prop_player: 'Jalen Hurts', prop_market: 'player_pass_yds',
      outcome: 'Over', line: 225.5,
    })
  })
  it('returns null for anything it cannot map', () => {
    expect(legFromPick('moneyline', 'Chiefs', 3)).toBeNull()
    expect(legFromPick('prop', 'A.J. Brown Yes', 3, 'player_anytime_td', 'A.J. Brown')).toBeNull()
    expect(legFromPick('parlay', 'HOME ML', 3)).toBeNull()
  })
})

describe('priceMoveNote', () => {
  it('says the price when it did not move', () => {
    expect(priceMoveNote(-110, -110)).toBe('Placed at -110')
  })
  it('says what it was when it moved (Review Focus 5)', () => {
    expect(priceMoveNote(-110, -115)).toBe('Placed at -115 — was -110 when you looked')
  })
})

describe('parlayEstimate', () => {
  it('matches the server formula for two -110 legs', () => {
    expect(parlayEstimate([-110, -110])?.american).toBe(264)
  })
  it('is null with fewer than two legs', () => {
    expect(parlayEstimate([-110])).toBeNull()
  })
})

describe('formatOdds and ageLabel', () => {
  it('signs positive prices', () => {
    expect(formatOdds(150)).toBe('+150')
    expect(formatOdds(-150)).toBe('-150')
  })
  it('describes quote age', () => {
    const now = new Date('2026-10-04T15:00:00Z')
    expect(ageLabel('2026-10-04T14:59:30+00:00', now)).toBe('Prices fetched just now')
    expect(ageLabel('2026-10-04T14:20:00+00:00', now)).toBe('Prices fetched 40m ago')
    expect(ageLabel('2026-10-04T12:00:00+00:00', now)).toBe('Prices fetched 3h ago')
  })
})

describe('findQuote', () => {
  const game: GameQuote[] = [
    { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -120, line: null,
      quoted_at: '2026-10-04T14:00:00+00:00', prop_player: null, prop_market: null },
    { pick_type: 'moneyline', side: 'AWAY', available: false, reason: 'stale', message: 'stale' },
  ]
  const props: PropQuote[] = [
    { prop_player: 'QB', prop_market: 'player_pass_yds', market_label: 'Pass Yards', outcome: 'Over', line: 225.5,
      available: true, pick_type: 'prop', pick_value: 'QB Over 225.5 Pass Yards', odds: -110,
      quoted_at: '2026-10-04T14:00:00+00:00' },
  ]
  it('finds the matching game side and prop line', () => {
    expect(findQuote(game, [], { game_id: 1, pick_type: 'moneyline', side: 'HOME' }))
      .toMatchObject({ available: true, odds: -120 })
    expect(findQuote([], props, { game_id: 1, pick_type: 'prop', prop_player: 'QB',
      prop_market: 'player_pass_yds', outcome: 'Over', line: 225.5 }))
      .toMatchObject({ available: true, odds: -110 })
  })
  it('returns the refusal for an unavailable side', () => {
    expect(findQuote(game, [], { game_id: 1, pick_type: 'moneyline', side: 'AWAY' })).toMatchObject({ available: false })
  })
})
