import { create } from 'zustand'
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware'
import type { BetLeg, Receipt, SlipLeg, SlipSelection } from '../types'
import { addOrReplaceLeg, marketKey, resolveLabel } from '../lib/quotes'
import { DEFAULT_STAKE, sameLeg } from '../lib/slip'

// Storage can throw (private mode, blocked site data): the slip then lives
// for this page only instead of breaking (spec §6, Review Focus 5).
const safeStorage: StateStorage = {
  getItem: (k) => { try { return localStorage.getItem(k) } catch { return null } },
  setItem: (k, v) => { try { localStorage.setItem(k, v) } catch { /* unavailable */ } },
  removeItem: (k) => { try { localStorage.removeItem(k) } catch { /* unavailable */ } },
}

export type SlipMode = 'singles' | 'parlay'

interface SlipData {
  legs: SlipLeg[]
  mode: SlipMode
  parlayStake: number
  sameStake: boolean
  acceptAnyOdds: boolean
  open: boolean
  receipt: Receipt | null
}

interface SlipActions {
  add: (s: SlipSelection) => void
  toggle: (s: SlipSelection) => void
  remove: (leg: BetLeg) => void
  setStake: (leg: BetLeg, stake: number) => void
  setMode: (mode: SlipMode) => void
  setParlayStake: (stake: number) => void
  setSameStake: (on: boolean) => void
  setAcceptAnyOdds: (on: boolean) => void
  setOpen: (open: boolean) => void
  priceMoved: (leg: BetLeg, odds: number, line: number | null, pickValue: string) => void
  setError: (leg: BetLeg, message: string | null) => void
  setReceipt: (receipt: Receipt | null) => void
  refill: (legs: SlipLeg[]) => void
  clear: () => void
}

export const SLIP_DEFAULTS: SlipData = {
  legs: [], mode: 'singles', parlayStake: DEFAULT_STAKE, sameStake: false,
  acceptAnyOdds: false, open: false, receipt: null,
}

// A saved slip is untrusted input: a hand edit or an older shape must not
// crash every page (the slip renders on all of them), so only well-formed
// legs and settings survive a reload (final review, Review Focus 5).
function isSavedLeg(l: unknown): l is SlipLeg {
  const x = l as Partial<SlipLeg> | null
  return typeof x === 'object' && x !== null && typeof x.leg === 'object' && x.leg !== null
    && typeof x.leg.game_id === 'number' && typeof x.leg.pick_type === 'string'
    && typeof x.odds === 'number' && Number.isFinite(x.odds)
    && typeof x.stake === 'number' && Number.isFinite(x.stake)
    && typeof x.label === 'string'
}

function savedSlip(saved: unknown): Partial<SlipData> {
  const s = (typeof saved === 'object' && saved !== null ? saved : {}) as Record<string, unknown>
  const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
  return {
    legs: Array.isArray(s.legs) ? s.legs.filter(isSavedLeg) : [],
    mode: s.mode === 'parlay' ? 'parlay' : 'singles',
    parlayStake: num(s.parlayStake, DEFAULT_STAKE),
    sameStake: s.sameStake === true,
    acceptAnyOdds: s.acceptAnyOdds === true,
  }
}

const onMarket = (leg: BetLeg) => (l: SlipLeg) => marketKey(l.leg) === marketKey(leg)

function newLeg(st: SlipData, s: SlipSelection): SlipLeg {
  const stake = st.sameStake && st.legs[0] ? st.legs[0].stake : DEFAULT_STAKE
  return { ...s, stake, movedFrom: null, error: null }
}

export const useSlip = create<SlipData & SlipActions>()(persist((set) => ({
  ...SLIP_DEFAULTS,
  add: (s) => set(st => ({ legs: addOrReplaceLeg(st.legs, newLeg(st, s)), receipt: null })),
  // A tap on a tile already on the slip takes it off, as on a real book;
  // the other side of that market swaps in (addOrReplaceLeg).
  toggle: (s) => set(st => (st.legs.some(l => sameLeg(l.leg, s.leg))
    ? { legs: st.legs.filter(l => !sameLeg(l.leg, s.leg)) }
    : { legs: addOrReplaceLeg(st.legs, newLeg(st, s)), receipt: null })),
  remove: (leg) => set(st => ({ legs: st.legs.filter(l => !onMarket(leg)(l)) })),
  setStake: (leg, stake) => set(st => ({
    legs: st.legs.map(l => (st.sameStake || onMarket(leg)(l) ? { ...l, stake } : l)) })),
  setMode: (mode) => set({ mode }),
  setParlayStake: (parlayStake) => set({ parlayStake }),
  setSameStake: (sameStake) => set(st => ({
    sameStake,
    legs: sameStake && st.legs[0] ? st.legs.map(l => ({ ...l, stake: st.legs[0].stake })) : st.legs })),
  setAcceptAnyOdds: (acceptAnyOdds) => set({ acceptAnyOdds }),
  setOpen: (open) => set({ open }),
  priceMoved: (leg, odds, line, pickValue) => set(st => ({
    legs: st.legs.map(l => (onMarket(leg)(l) ? {
      ...l,
      movedFrom: l.movedFrom ?? { odds: l.odds, line: l.line },
      odds, line, error: null,
      // A game label is rebuilt from the server's ("AWAY +3.5" -> "Cowboys
      // +3.5"); a prop's line can't move, so its label stands.
      label: l.leg.pick_type === 'prop' || !l.homeTeam ? l.label : resolveLabel(pickValue, l.homeTeam, l.awayTeam),
    } : l)) })),
  setError: (leg, error) => set(st => ({ legs: st.legs.map(l => (onMarket(leg)(l) ? { ...l, error } : l)) })),
  setReceipt: (receipt) => set({ receipt }),
  refill: (legs) => set(st => ({
    legs: legs.reduce((acc, l) => addOrReplaceLeg(acc, { ...l, movedFrom: null, error: null }), st.legs) })),
  clear: () => set({ legs: [] }),
}), {
  name: 'sp-slip',
  version: 1,
  migrate: (saved) => saved as SlipData,
  merge: (saved, current) => ({ ...current, ...savedSlip(saved) }),
  storage: createJSONStorage(() => safeStorage),
  partialize: (st) => ({ legs: st.legs, mode: st.mode, parlayStake: st.parlayStake,
    sameStake: st.sameStake, acceptAnyOdds: st.acceptAnyOdds }),
}))
