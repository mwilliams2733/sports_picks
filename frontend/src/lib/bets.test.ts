import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import type { Ticket, TicketGame, TicketLeg } from '../types'
import { filterSettled, gameLine, legLabel, legStatus, loadSeen, newWins, saveSeen, signedMoney,
  splitTickets, ticketCode, ticketKey, ticketType } from './bets'

const game = (over: Partial<TicketGame> = {}): TicketGame => ({
  id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
  status: 'scheduled', home_score: null, away_score: null, live_detail: null, ...over,
})
const leg = (over: Partial<TicketLeg> = {}): TicketLeg => ({
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null,
  result: null, game: game(), ...over,
})
const ticket = (over: Partial<Ticket> = {}): Ticket => ({
  kind: 'straight', id: 6, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg()], ...over,
})

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('ticket labels', () => {
  it('codes and types a ticket the way the receipt does', () => {
    expect(ticketCode(ticket())).toBe('#P-6')
    expect(ticketCode(ticket({ kind: 'parlay', id: 4 }))).toBe('#PL-4')
    expect(ticketType(ticket())).toBe('Straight')
    expect(ticketType(ticket({ kind: 'parlay' }))).toBe('Parlay')
    expect(ticketType(ticket({ kind: 'parlay', sgp: true }))).toBe('SGP')
    expect(ticketKey(ticket({ kind: 'parlay', id: 4 }))).toBe('parlay-4')
  })
})

describe('splitTickets / filterSettled', () => {
  const open = ticket({ id: 1 })
  const won = ticket({ id: 2, result: 'win', payout: 91.74 })
  const lost = ticket({ id: 3, result: 'loss', payout: -100 })
  const push = ticket({ id: 4, result: 'push', payout: 0 })
  it('splits open from settled, keeping order', () => {
    expect(splitTickets([open, won, lost])).toEqual({ open: [open], settled: [won, lost] })
  })
  it('filters settled by result', () => {
    expect(filterSettled([won, lost, push], 'all')).toEqual([won, lost, push])
    expect(filterSettled([won, lost, push], 'won')).toEqual([won])
    expect(filterSettled([won, lost, push], 'lost')).toEqual([lost])
  })
})

describe('legs', () => {
  it('names a game leg by team and a prop leg as stored', () => {
    expect(legLabel(leg())).toBe('TB +9')
    expect(legLabel(leg({ pick_type: 'moneyline', pick_value: 'HOME ML' }))).toBe('DAL ML')
    expect(legLabel(leg({ pick_type: 'prop', pick_value: 'Dak Prescott Over 255.5 Pass Yards' })))
      .toBe('Dak Prescott Over 255.5 Pass Yards')
  })
  it('maps a leg result to a status dot', () => {
    expect([null, 'win', 'loss', 'push'].map(r => legStatus(leg({ result: r }))))
      .toEqual(['pending', 'won', 'lost', 'push'])
  })
  it('shows a final score, the kickoff, or an unusual status (Review Focus 5)', () => {
    expect(gameLine(game({ status: 'final', home_score: 27, away_score: 20 }))).toBe('TB 20 – DAL 27 · Final')
    expect(gameLine(game())).toMatch(/^TB @ DAL · \d{1,2}:\d{2}\s?[AP]M$/)
    expect(gameLine(game({ status: 'postponed' }))).toBe('TB @ DAL · Postponed')
  })
})

describe('signedMoney', () => {
  it('never prints +− (Review Focus 4)', () => {
    expect(signedMoney(91.74)).toBe('+$91.74')
    expect(signedMoney(-100)).toBe('−$100.00')
    expect(signedMoney(0)).toBe('$0.00')
  })
})

describe('celebrations: newWins / seen storage (Review Focus 2)', () => {
  const won = ticket({ id: 2, result: 'win', payout: 91.74 })
  const lost = ticket({ id: 3, result: 'loss', payout: -100 })
  it('seeds silently on a first visit, so past wins never replay', () => {
    expect(newWins([won, lost, ticket()], null)).toEqual({ wins: [], seen: ['straight-2', 'straight-3'] })
  })
  it('reports a win that settles after the first visit, once', () => {
    const later = ticket({ id: 7, result: 'win', payout: 50 })
    const first = newWins([won, later], ['straight-2'])
    expect(first.wins).toEqual([later])
    expect(newWins([won, later], first.seen).wins).toEqual([])
  })
  it('never celebrates a loss', () => {
    expect(newWins([lost], []).wins).toEqual([])
  })
  it('reads corrupt or missing storage as a first visit', () => {
    expect(loadSeen(1)).toBeNull()
    localStorage.setItem('sp-seen-wins.1', '{bad')
    expect(loadSeen(1)).toBeNull()
    saveSeen(1, ['straight-2'])
    expect(loadSeen(1)).toEqual(['straight-2'])
  })
  it('survives storage that throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => saveSeen(1, ['x'])).not.toThrow()
  })
})
