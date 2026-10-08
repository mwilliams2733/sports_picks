import { ApiError } from '../api/client'
import type { BetLeg, BetRequest, SlipLeg, SlipSelection } from '../types'
import { legFromPick, marketKey } from './quotes'

export const QUICK_STAKES = [25, 50, 100] as const
export const DEFAULT_STAKE = 25

/** Two legs are the same bet: the same market and the same side (for a prop,
 *  the same outcome and line). */
export function sameLeg(a: BetLeg, b: BetLeg): boolean {
  if (marketKey(a) !== marketKey(b)) return false
  if (a.pick_type === 'prop' && b.pick_type === 'prop') return a.outcome === b.outcome && a.line === b.line
  return a.pick_type !== 'prop' && b.pick_type !== 'prop' && a.side === b.side
}

/** The line a stored model pick label carries ("AWAY +3.5" -> 3.5,
 *  "Over 7" -> 7); null for a moneyline. */
export function lineFromPick(pickType: string, value: string): number | null {
  if (pickType !== 'spread' && pickType !== 'over_under') return null
  const m = value.match(/([+-]?\d+(?:\.\d+)?)\s*$/)
  return m ? Number(m[1]) : null
}

export interface PickInput {
  pickType: string
  /** The stored label legFromPick maps ("HOME ML"); undefined when the page
   *  only has a display label ("NYY ML"), which can't be bet. */
  value: string | undefined
  label: string
  gameId: number
  odds: number
  propMarket?: string
  propPlayer?: string
  homeTeam?: string
  awayTeam?: string
  gameLabel?: string
  startTime?: string | null
}

/** A Research-page model pick as a slip selection, at the price the model saw
 *  -- the server's price_moved refusal tells the player if it has moved. */
export function selectionFromPick(p: PickInput): SlipSelection | null {
  if (p.value === undefined) return null
  const leg = legFromPick(p.pickType, p.value, p.gameId, p.propMarket, p.propPlayer)
  if (!leg) return null
  const home = p.homeTeam ?? ''
  const away = p.awayTeam ?? ''
  return {
    leg,
    label: p.label,
    gameLabel: p.gameLabel ?? (home && away ? `${away} @ ${home}` : ''),
    startTime: p.startTime ?? null,
    odds: p.odds,
    line: leg.pick_type === 'prop' ? leg.line : lineFromPick(p.pickType, p.value),
    homeTeam: home,
    awayTeam: away,
  }
}

/** Profit on a winning bet at American odds. */
export function toWin(stake: number, odds: number): number {
  return odds > 0 ? (stake * odds) / 100 : (stake * 100) / Math.abs(odds)
}

/** A start time as an instant. /games/today sends naive UTC ("…T17:00:00"),
 *  which Date would read as local time -- hours off for every viewer. */
export function startInstant(s: string): number {
  return new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`).getTime()
}

export function isClosed(l: { startTime: string | null }, now: Date = new Date()): boolean {
  return l.startTime !== null && startInstant(l.startTime) <= now.getTime()
}

export function hasSameGame(legs: SlipLeg[]): boolean {
  const ids = legs.map(l => l.leg.game_id)
  return new Set(ids).size !== ids.length
}

/** The request for one leg. Props send no expected_line: the line is part of
 *  a prop bet, and the server refuses one with 422. */
export function betRequest(l: SlipLeg, acceptAnyOdds: boolean): BetRequest {
  if (acceptAnyOdds) return { ...l.leg }
  if (l.leg.pick_type === 'prop') return { ...l.leg, expected_odds: l.odds }
  return { ...l.leg, expected_odds: l.odds, expected_line: l.line }
}

export type Refusal =
  | { kind: 'moved'; odds: number; line: number | null; pickValue: string; leg?: number }
  | { kind: 'pin'; message: string }
  | { kind: 'error'; message: string }

/** What a failed bet POST means for the slip. Known refusals carry the
 *  server's own message; a network failure keeps the slip. */
export function refusal(e: unknown): Refusal {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: unknown } | null)?.detail
    if (e.status === 409 && typeof d === 'object' && d !== null
        && (d as { reason?: unknown }).reason === 'price_moved') {
      const m = d as { odds: number; line: number | null; pick_value: string; leg?: number }
      return { kind: 'moved', odds: m.odds, line: m.line, pickValue: m.pick_value, leg: m.leg }
    }
    if (e.status === 401) return { kind: 'pin', message: e.message }
    return { kind: 'error', message: e.message }
  }
  return { kind: 'error', message: "Couldn't reach the server — your slip is kept." }
}
