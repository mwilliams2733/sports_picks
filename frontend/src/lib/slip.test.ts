import { describe, it, expect } from 'vitest'
import { ApiError } from '../api/client'
import type { SlipLeg } from '../types'
import { betRequest, hasSameGame, isClosed, lineFromPick, refusal, sameLeg, selectionFromPick, toWin } from './slip'

const slipLeg = (over: Partial<SlipLeg> = {}): SlipLeg => ({
  leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'Cowboys +3', gameLabel: 'Cowboys @ Bucs',
  startTime: null, odds: -110, line: 3, homeTeam: 'Bucs', awayTeam: 'Cowboys', stake: 25, ...over,
})

describe('sameLeg', () => {
  it('is the same market and the same side', () => {
    const a = { game_id: 1, pick_type: 'spread' as const, side: 'AWAY' as const }
    expect(sameLeg(a, { ...a })).toBe(true)
    expect(sameLeg(a, { ...a, side: 'HOME' })).toBe(false)
    expect(sameLeg(a, { ...a, game_id: 2 })).toBe(false)
  })
  it('compares a prop by outcome and line', () => {
    const p = { game_id: 1, pick_type: 'prop' as const, prop_player: 'QB', prop_market: 'player_pass_yds',
      outcome: 'Over' as const, line: 225.5 }
    expect(sameLeg(p, { ...p })).toBe(true)
    expect(sameLeg(p, { ...p, line: 230.5 })).toBe(false)
    expect(sameLeg(p, { ...p, outcome: 'Under' })).toBe(false)
  })
})

describe('lineFromPick', () => {
  it('reads the line a stored model pick label carries', () => {
    expect(lineFromPick('spread', 'AWAY +6.5')).toBe(6.5)
    expect(lineFromPick('spread', 'HOME -1.5')).toBe(-1.5)
    expect(lineFromPick('over_under', 'Over 7')).toBe(7)
    expect(lineFromPick('moneyline', 'HOME ML')).toBeNull()
  })
})

describe('selectionFromPick', () => {
  it('maps a stored label to a side at the price the model saw', () => {
    expect(selectionFromPick({ pickType: 'moneyline', value: 'HOME ML', label: 'NYY ML', gameId: 1, odds: -131,
      homeTeam: 'NYY', awayTeam: 'BOS', startTime: '2099-01-01T00:00:00Z' })).toEqual({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, label: 'NYY ML', gameLabel: 'BOS @ NYY',
      startTime: '2099-01-01T00:00:00Z', odds: -131, line: null, homeTeam: 'NYY', awayTeam: 'BOS' })
  })
  it('is null without a stored label, or when the label maps to no side', () => {
    expect(selectionFromPick({ pickType: 'moneyline', value: undefined, label: 'NYY ML', gameId: 1, odds: -131 })).toBeNull()
    expect(selectionFromPick({ pickType: 'moneyline', value: 'NYY ML', label: 'NYY ML', gameId: 1, odds: -131 })).toBeNull()
  })
  it('takes a prop line from the label', () => {
    expect(selectionFromPick({ pickType: 'prop', value: 'Jayson Tatum Over 27.5', label: 'Jayson Tatum Over 27.5 Points',
      gameId: 4, odds: -110, propMarket: 'player_points', propPlayer: 'Jayson Tatum', gameLabel: 'BOS @ NYY' }))
      .toMatchObject({ leg: { pick_type: 'prop', line: 27.5, outcome: 'Over' }, line: 27.5, gameLabel: 'BOS @ NYY' })
  })
})

describe('toWin / isClosed / hasSameGame', () => {
  it('pays American odds', () => {
    expect(toWin(100, 150)).toBe(150)
    expect(toWin(110, -110)).toBe(100)
  })
  it('closes a leg at its start time', () => {
    const now = new Date('2026-10-11T17:00:00Z')
    expect(isClosed({ startTime: '2026-10-11T17:00:00Z' }, now)).toBe(true)
    expect(isClosed({ startTime: '2026-10-11T17:01:00Z' }, now)).toBe(false)
    expect(isClosed({ startTime: null }, now)).toBe(false)
  })
  it('spots two legs on one game (an SGP)', () => {
    expect(hasSameGame([slipLeg(), slipLeg({ leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' } })])).toBe(true)
    expect(hasSameGame([slipLeg(), slipLeg({ leg: { game_id: 2, pick_type: 'moneyline', side: 'HOME' } })])).toBe(false)
  })
})

describe('betRequest', () => {
  it('sends the price and line the player saw', () => {
    expect(betRequest(slipLeg(), false)).toEqual({ game_id: 1, pick_type: 'spread', side: 'AWAY',
      expected_odds: -110, expected_line: 3 })
    expect(betRequest(slipLeg({ leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, line: null }), false))
      .toEqual({ game_id: 1, pick_type: 'moneyline', side: 'HOME', expected_odds: -110, expected_line: null })
  })
  it('sends no line for a prop', () => {
    const leg = { game_id: 1, pick_type: 'prop' as const, prop_player: 'QB', prop_market: 'player_pass_yds',
      outcome: 'Over' as const, line: 225.5 }
    expect(betRequest(slipLeg({ leg, line: 225.5 }), false)).toEqual({ ...leg, expected_odds: -110 })
  })
  it('sends the bare leg when any odds change is accepted', () => {
    expect(betRequest(slipLeg(), true)).toEqual({ game_id: 1, pick_type: 'spread', side: 'AWAY' })
  })
})

describe('refusal', () => {
  it('reads a price_moved 409', () => {
    const e = new ApiError(409, { detail: { reason: 'price_moved', message: 'm', odds: -125, line: 3.5,
      pick_value: 'AWAY +3.5', leg: 1 } })
    expect(refusal(e)).toEqual({ kind: 'moved', odds: -125, line: 3.5, pickValue: 'AWAY +3.5', leg: 1 })
  })
  it('tells a PIN failure from other refusals', () => {
    expect(refusal(new ApiError(401, { detail: 'Wrong PIN' }))).toEqual({ kind: 'pin', message: 'Wrong PIN' })
    expect(refusal(new ApiError(409, { detail: 'The price is stale — ask Marcus to refresh.' })))
      .toEqual({ kind: 'error', message: 'The price is stale — ask Marcus to refresh.' })
  })
  it('keeps the slip on a network failure', () => {
    expect(refusal(new TypeError('Failed to fetch')))
      .toEqual({ kind: 'error', message: "Couldn't reach the server — your slip is kept." })
  })
})
