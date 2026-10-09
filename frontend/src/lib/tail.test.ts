import { describe, it, expect, beforeEach } from 'vitest'
import { tailLegs, tailBet } from './tail'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import type { FeedLeg, GameQuote, PropQuote } from '../types'

const leg = (over: Partial<FeedLeg> = {}): FeedLeg => ({
  game_id: 7, pick_type: 'spread', side: 'AWAY', label: 'TB +9', game_label: 'TB @ DAL',
  start_time: '2099-01-01T00:00:00+00:00', home_team: 'DAL', away_team: 'TB', odds: -109, quoted_line: 9, ...over,
} as FeedLeg)
const quote = (over: Partial<GameQuote> = {}): GameQuote => ({
  pick_type: 'spread', side: 'AWAY', available: true, pick_value: 'AWAY +8.5', odds: -115, line: 8.5,
  quoted_at: 'x', prop_player: null, prop_market: null, ...over,
} as GameQuote)
const fetchers = (game: GameQuote[], props: PropQuote[] = []) => ({
  quotes: async () => ({ game_id: 7, quotes: game }),
  propQuotes: async () => ({ game_id: 7, quotes: props }),
})

beforeEach(() => useSlip.setState({ ...SLIP_DEFAULTS }))

describe('tailLegs', () => {
  it("re-prices at today's quote, not the friend's price (Review Focus 3)", async () => {
    const [r] = await tailLegs([leg()], fetchers([quote()]))
    expect(r.error).toBeNull()
    expect(r.selection).toMatchObject({ leg: { game_id: 7, pick_type: 'spread', side: 'AWAY' },
      label: 'TB +8.5', odds: -115, line: 8.5, gameLabel: 'TB @ DAL', homeTeam: 'DAL', awayTeam: 'TB' })
  })
  it('locks a leg the server now refuses, with its message', async () => {
    const refused = { pick_type: 'spread', side: 'AWAY', available: false, reason: 'game_started',
      message: 'Betting has closed: this game has already started' } as GameQuote
    const [r] = await tailLegs([leg()], fetchers([refused]))
    expect(r.error).toBe('Betting has closed: this game has already started')
    expect(r.selection.odds).toBe(-109)                 // shown for reference only; the server re-prices
    await tailBet([leg()], fetchers([refused]))
    expect(useSlip.getState().legs[0]).toMatchObject({ error: 'Betting has closed: this game has already started', locked: true })
  })
  it('locks a leg no book quotes any more', async () => {
    const [r] = await tailLegs([leg()], fetchers([]))
    expect(r.error).toBe('No book is quoting this bet right now.')
  })
})

describe('tailBet', () => {
  it('puts the legs on your slip, swapping a market already there (Review Focus 4)', async () => {
    useSlip.getState().add({ leg: { game_id: 7, pick_type: 'spread', side: 'HOME' }, label: 'DAL -9',
      gameLabel: 'TB @ DAL', startTime: null, odds: -110, line: -9, homeTeam: 'DAL', awayTeam: 'TB' })
    const live = await tailBet([leg()], fetchers([quote()]))
    expect(live).toBe(1)
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['TB +8.5'])
    expect(useSlip.getState().open).toBe(true)
  })
})
