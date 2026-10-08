import { describe, it, expect } from 'vitest'
import type { AvailableGameQuote, BoardGame, GameQuote, PropQuote } from '../types'
import { etToday, formatDay, formatMoney, gameTarget, groupByDay, groupProps, modelPickQuote, modelPickTarget,
  priceMove, propTarget, sportTabs, startLabel, tileTop, findGameQuote } from './board'

const q = (pick_type: string, side: string, extra: Partial<AvailableGameQuote> = {}): GameQuote => ({
  pick_type, side, available: true, pick_value: `${side} x`, odds: -110, line: null,
  quoted_at: '2026-10-10T12:00:00+00:00', prop_player: null, prop_market: null, ...extra,
} as GameQuote)

const game = (over: Partial<BoardGame> = {}): BoardGame => ({
  id: 1, sport: 'nfl', date: '2026-10-11', start_time: '2026-10-11T17:00:00+00:00',
  home_team: 'Buccaneers', away_team: 'Cowboys', prop_count: 3, model_pick: null,
  quotes: [
    q('spread', 'HOME', { line: -3, pick_value: 'HOME -3' }),
    q('spread', 'AWAY', { line: 3, pick_value: 'AWAY +3' }),
    q('moneyline', 'HOME', { odds: -160, pick_value: 'HOME ML' }),
    q('moneyline', 'AWAY', { odds: 135, pick_value: 'AWAY ML' }),
    q('over_under', 'Over', { line: 47.5, pick_value: 'Over 47.5' }),
    { pick_type: 'over_under', side: 'Under', available: false, reason: 'stale',
      message: 'The price is stale — ask Marcus to refresh.' },
  ],
  ...over,
})

describe('etToday', () => {
  it('is the ET date, not the UTC date, across midnight', () => {
    expect(etToday(new Date('2026-10-11T03:30:00Z'))).toBe('2026-10-10')
    expect(etToday(new Date('2026-10-11T16:00:00Z'))).toBe('2026-10-11')
    // 00:30 ET on the 11th is still the 10th in the Pacific (and in most browsers' zones west of ET)
    expect(etToday(new Date('2026-10-11T04:30:00Z'))).toBe('2026-10-11')
  })
})

describe('formatDay / groupByDay', () => {
  it('formats a date as weekday month day', () => {
    expect(formatDay('2026-10-11')).toBe('Sun Oct 11')
  })
  it('labels today "Today" and keeps board order within a day', () => {
    const a = game({ id: 1, date: '2026-10-10' })
    const b = game({ id: 2, date: '2026-10-11' })
    const c = game({ id: 3, date: '2026-10-11' })
    expect(groupByDay([a, b, c], '2026-10-10')).toEqual([
      { date: '2026-10-10', label: 'Today', games: [a] },
      { date: '2026-10-11', label: 'Sun Oct 11', games: [b, c] },
    ])
  })
})

describe('startLabel', () => {
  it('is TBD with no kickoff', () => expect(startLabel(null)).toBe('TBD'))
  it('is a clock time otherwise', () => expect(startLabel('2026-10-11T17:00:00+00:00')).toMatch(/\d{1,2}:\d{2}\s?[AP]M/))
})

describe('formatMoney', () => {
  it('uses dollars, thousands separators and cents', () => expect(formatMoney(10240.5)).toBe('$10,240.50'))
})

describe('sportTabs', () => {
  it('lists each sport once, in board order', () => {
    expect(sportTabs([game({ sport: 'nba' }), game({ sport: 'nfl' }), game({ sport: 'nba' })]))
      .toEqual(['nba', 'nfl'])
  })
})

describe('tileTop', () => {
  const g = game()
  const avail = (pt: string, side: string) => findGameQuote(g.quotes, pt as never, side as never) as AvailableGameQuote
  it('signs a spread line', () => {
    expect(tileTop(avail('spread', 'AWAY'))).toBe('+3')
    expect(tileTop(avail('spread', 'HOME'))).toBe('-3')
  })
  it('prefixes a total with O/U', () => expect(tileTop(avail('over_under', 'Over'))).toBe('O 47.5'))
  it('has no top line for a moneyline', () => expect(tileTop(avail('moneyline', 'HOME'))).toBeNull())
})

describe('priceMove', () => {
  it('is null with nothing to compare or no change', () => {
    expect(priceMove(null, { odds: -110, line: 3 })).toBeNull()
    expect(priceMove({ odds: -110, line: 3 }, { odds: -110, line: 3 })).toBeNull()
  })
  it('higher American odds are better for the bettor', () => {
    expect(priceMove({ odds: -110, line: null }, { odds: -105, line: null })).toBe('better')
    expect(priceMove({ odds: 130, line: null }, { odds: 120, line: null })).toBe('worse')
  })
  it('a line change is a move with no direction', () => {
    expect(priceMove({ odds: -110, line: 3 }, { odds: -120, line: 3.5 })).toBe('moved')
  })
})

describe('gameTarget / modelPickQuote', () => {
  it('builds BetModal props from a quote, team-resolved', () => {
    const g = game()
    const t = gameTarget(g, findGameQuote(g.quotes, 'spread', 'AWAY') as AvailableGameQuote, 4.1)
    expect(t).toEqual({ pickType: 'spread', pickValue: 'Cowboys +3', betValue: 'AWAY +3', odds: -110,
      gameId: 1, homeTeam: 'Buccaneers', awayTeam: 'Cowboys', edgePct: 4.1, priceSource: 'board' })
  })
  it('marks a board tile target as a board price, not the model', () => {
    const g = game()
    expect(gameTarget(g, findGameQuote(g.quotes, 'moneyline', 'HOME') as AvailableGameQuote).priceSource).toBe('board')
  })
  it('bets a model pick at the price and line the model evaluated, so BetModal notes any move', () => {
    const g = game({ model_pick: { pick_type: 'spread', pick_value: 'AWAY +3.5', odds: 120, edge_pct: 4.1 } })
    expect(modelPickTarget(g, findGameQuote(g.quotes, 'spread', 'AWAY') as AvailableGameQuote)).toEqual({
      pickType: 'spread', pickValue: 'Cowboys +3.5', betValue: 'AWAY +3.5', odds: 120, gameId: 1,
      homeTeam: 'Buccaneers', awayTeam: 'Cowboys', edgePct: 4.1, priceSource: 'model' })
  })
  it("finds the model pick's live quote, refused or not", () => {
    expect(modelPickQuote(game({ model_pick: { pick_type: 'over_under', pick_value: 'Under 47.5', odds: -110, edge_pct: 3 } })))
      .toMatchObject({ side: 'Under', available: false })
  })
  it('is null when the stored label maps to no side', () => {
    expect(modelPickQuote(game({ model_pick: { pick_type: 'moneyline', pick_value: 'Cowboys ML', odds: 135, edge_pct: 3 } })))
      .toBeNull()
    expect(modelPickQuote(game())).toBeNull()
  })
})

describe('groupProps / propTarget', () => {
  const p = (player: string, market_label: string, outcome: 'Over' | 'Under', line: number, odds = -110): PropQuote => ({
    available: true, pick_type: 'prop', pick_value: `${player} ${outcome} ${line} ${market_label}`, odds,
    quoted_at: 'x', prop_player: player, prop_market: 'player_pass_yds', market_label, outcome, line,
  })
  it('groups by market, pairs Over and Under on the same player and line', () => {
    const over = p('QB One', 'Pass Yds', 'Over', 245.5, -115)
    const under = p('QB One', 'Pass Yds', 'Under', 245.5, -105)
    const rec = p('WR Two', 'Rec Yds', 'Over', 60.5)
    expect(groupProps([over, rec, under])).toEqual([
      ['Pass Yds', [{ player: 'QB One', line: 245.5, over, under }]],
      ['Rec Yds', [{ player: 'WR Two', line: 60.5, over: rec, under: undefined }]],
    ])
  })
  it('builds a prop BetModal target that legFromPick can parse', () => {
    const over = p('QB One', 'Pass Yds', 'Over', 245.5, -115)
    expect(propTarget(9, over as never)).toEqual({ pickType: 'prop', pickValue: 'QB One Over 245.5',
      betValue: 'QB One Over 245.5', odds: -115, gameId: 9, homeTeam: '', awayTeam: '',
      propMarket: 'player_pass_yds', propPlayer: 'QB One' })
  })
})
