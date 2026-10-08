import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useSlip, SLIP_DEFAULTS } from './slipStore'
import type { SlipSelection } from '../types'

const sel = (side: 'HOME' | 'AWAY', game_id = 1): SlipSelection => ({
  leg: { game_id, pick_type: 'spread', side }, label: side === 'HOME' ? 'Bucs -3' : 'Cowboys +3',
  gameLabel: 'Cowboys @ Bucs', startTime: null, odds: -110, line: side === 'HOME' ? -3 : 3,
  homeTeam: 'Bucs', awayTeam: 'Cowboys',
})

beforeEach(() => {
  localStorage.clear()
  useSlip.setState({ ...SLIP_DEFAULTS })
})
afterEach(() => vi.restoreAllMocks())

describe('slip store', () => {
  it('toggle adds at the default stake, and the same tile again removes it', () => {
    useSlip.getState().toggle(sel('AWAY'))
    expect(useSlip.getState().legs).toEqual([{ ...sel('AWAY'), stake: 25, movedFrom: null, error: null }])
    useSlip.getState().toggle(sel('AWAY'))
    expect(useSlip.getState().legs).toEqual([])
  })

  it('the other side of a market already on the slip swaps it', () => {
    useSlip.getState().toggle(sel('AWAY'))
    useSlip.getState().toggle(sel('HOME'))
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['Bucs -3'])
  })

  it('add never removes, and clears a showing receipt', () => {
    useSlip.setState({ receipt: { bets: [], legs: [], placedAt: 'x' } })
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().add(sel('AWAY'))
    expect(useSlip.getState().legs).toHaveLength(1)
    expect(useSlip.getState().receipt).toBeNull()
  })

  it('same stake for all moves every leg together', () => {
    useSlip.getState().add(sel('AWAY', 1))
    useSlip.getState().add(sel('AWAY', 2))
    useSlip.getState().setSameStake(true)
    useSlip.getState().setStake(sel('AWAY', 1).leg, 80)
    expect(useSlip.getState().legs.map(l => l.stake)).toEqual([80, 80])
  })

  it('a price move keeps the first price seen and relabels from the server label', () => {
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().priceMoved(sel('AWAY').leg, -120, 3.5, 'AWAY +3.5')
    useSlip.getState().priceMoved(sel('AWAY').leg, -125, 3.5, 'AWAY +3.5')
    expect(useSlip.getState().legs[0]).toMatchObject({
      odds: -125, line: 3.5, label: 'Cowboys +3.5', movedFrom: { odds: -110, line: 3 } })
  })

  it('persists legs and the odds-change setting, not the open sheet', () => {
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().setAcceptAnyOdds(true)
    useSlip.getState().setOpen(true)
    const saved = JSON.parse(localStorage.getItem('sp-slip')!).state
    expect(saved.legs).toHaveLength(1)
    expect(saved.acceptAnyOdds).toBe(true)
    expect(saved.open).toBeUndefined()
  })

  it('keeps working in memory when storage throws (Review Focus 5)', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => useSlip.getState().add(sel('AWAY'))).not.toThrow()
    expect(useSlip.getState().legs).toHaveLength(1)
  })

  it('ignores a corrupt saved slip (Review Focus 5)', async () => {
    localStorage.setItem('sp-slip', '{not json')
    await useSlip.persist.rehydrate()
    expect(useSlip.getState().legs).toEqual([])
  })

  it('drops a saved slip in the wrong shape instead of crashing every page (final review)', async () => {
    useSlip.getState().add(sel('AWAY'))
    localStorage.setItem('sp-slip', JSON.stringify({ state: { legs: [{}, { leg: { game_id: 1 } }, null] }, version: 0 }))
    await useSlip.persist.rehydrate()
    expect(useSlip.getState().legs).toEqual([])
    localStorage.setItem('sp-slip', JSON.stringify({ state: { legs: null, mode: 'x', parlayStake: 'NaN' }, version: 0 }))
    await useSlip.persist.rehydrate()
    expect(useSlip.getState()).toMatchObject({ legs: [], mode: 'singles', parlayStake: 25 })
  })

  it('keeps well-formed saved legs on rehydrate', async () => {
    useSlip.getState().add(sel('AWAY'))
    useSlip.setState({ legs: [] })
    localStorage.setItem('sp-slip', JSON.stringify({ state: { legs: [{ ...sel('AWAY'), stake: 40 }] }, version: 1 }))
    await useSlip.persist.rehydrate()
    expect(useSlip.getState().legs.map(l => l.stake)).toEqual([40])
  })
})
