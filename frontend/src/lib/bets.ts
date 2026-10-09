import type { Ticket, TicketGame, TicketLeg } from '../types'
import { legFromPick, resolveLabel } from './quotes'
import { etToday, formatDay, formatMoney, startLabel } from './board'
import { lineFromPick, startInstant } from './slip'

export type SettledFilter = 'all' | 'won' | 'lost'
export type LegStatus = 'pending' | 'won' | 'lost' | 'push'

export const ticketKey = (t: Ticket) => `${t.kind}-${t.id}`
export const ticketCode = (t: Ticket) => (t.kind === 'parlay' ? `#PL-${t.id}` : `#P-${t.id}`)
export const ticketType = (t: Ticket) => (t.kind === 'straight' ? 'Straight' : t.sgp ? 'SGP' : 'Parlay')

export function splitTickets(tickets: Ticket[]): { open: Ticket[]; settled: Ticket[] } {
  return { open: tickets.filter(t => t.result === null), settled: tickets.filter(t => t.result !== null) }
}

export function filterSettled(tickets: Ticket[], f: SettledFilter): Ticket[] {
  if (f === 'won') return tickets.filter(t => t.result === 'win')
  if (f === 'lost') return tickets.filter(t => t.result === 'loss')
  return tickets
}

export function legLabel(l: TicketLeg): string {
  return l.pick_type === 'prop' ? l.pick_value : resolveLabel(l.pick_value, l.game.home_team, l.game.away_team)
}

export function legStatus(l: TicketLeg): LegStatus {
  return l.result === 'win' ? 'won' : l.result === 'loss' ? 'lost' : l.result === 'push' ? 'push' : 'pending'
}

export type LegTint = 'winning' | 'losing' | 'even'

/** Under way: marked in_progress by the live job, or past its kickoff without
 *  a result (combat sports get no live feed). */
export function isLive(g: TicketGame, now: Date = new Date()): boolean {
  // ESPN has called it ("Final", "Final/OT"): finished, waiting on the 08:00
  // results pass to be graded -- not live.
  if (g.status === 'in_progress') return !(g.live_detail ?? '').startsWith('Final')
  return g.status === 'scheduled' && g.start_time !== null && startInstant(g.start_time) <= now.getTime()
}

/** Winning / Losing / Even for a live game leg from the running score and the
 *  leg's line; null for props, settled legs and games without a live score. */
export function legTint(l: TicketLeg): LegTint | null {
  const g = l.game
  if (l.result !== null || g.status !== 'in_progress' || !isLive(g)
      || g.home_score === null || g.away_score === null) return null
  const leg = legFromPick(l.pick_type, l.pick_value, g.id)
  if (!leg || leg.pick_type === 'prop') return null
  let edge: number
  if (leg.pick_type === 'over_under') {
    const line = lineFromPick('over_under', l.pick_value)
    if (line === null) return null
    const total = g.home_score + g.away_score
    edge = leg.side === 'Over' ? total - line : line - total
  } else {
    const margin = leg.side === 'HOME' ? g.home_score - g.away_score : g.away_score - g.home_score
    edge = leg.pick_type === 'spread' ? margin + (lineFromPick('spread', l.pick_value) ?? 0) : margin
  }
  return edge > 0 ? 'winning' : edge < 0 ? 'losing' : 'even'
}

/** A kickoff as "8:15 PM" today (ET), or "Sun Oct 11, 1:00 PM" on another
 *  day -- a ticket can be for a game up to a week out. */
function kickoffLabel(start: string | null, now: Date): string {
  if (!start) return startLabel(null)
  const day = etToday(new Date(start))
  return day === etToday(now) ? startLabel(start) : `${formatDay(day)}, ${startLabel(start)}`
}

/** "TB 20 – DAL 27 · Final", "TB @ DAL · 8:15 PM", or "TB @ DAL · Postponed". */
export function gameLine(g: TicketGame, now: Date = new Date()): string {
  if (g.status === 'final' && g.home_score !== null && g.away_score !== null) {
    return `${g.away_team} ${g.away_score} – ${g.home_team} ${g.home_score} · Final`
  }
  if (g.status === 'in_progress') {
    return g.home_score !== null && g.away_score !== null
      ? `${g.away_team} ${g.away_score} – ${g.home_team} ${g.home_score} · ${g.live_detail ?? 'Live'}`
      : `${g.away_team} @ ${g.home_team} · Live`
  }
  if (isLive(g, now)) return `${g.away_team} @ ${g.home_team} · Live`
  const when = g.status === 'scheduled' ? kickoffLabel(g.start_time, now)
    : g.status.charAt(0).toUpperCase() + g.status.slice(1).replace(/_/g, ' ')
  return `${g.away_team} @ ${g.home_team} · ${when}`
}

/** "+$91.74", "−$100.00" (a real minus sign), "$0.00". */
export function signedMoney(n: number): string {
  if (n > 0) return `+${formatMoney(n)}`
  if (n < 0) return `−${formatMoney(Math.abs(n))}`
  return formatMoney(0)
}

/** Wins to celebrate and the new seen set. `seen === null` is a first visit
 *  on this device: everything already settled is marked seen silently, so a
 *  new phone never replays a player's whole history (spec §7). */
export function newWins(tickets: Ticket[], seen: string[] | null): { wins: Ticket[]; seen: string[] } {
  const settled = tickets.filter(t => t.result !== null).map(ticketKey)
  if (seen === null) return { wins: [], seen: settled }
  const known = new Set(seen)
  return {
    wins: tickets.filter(t => t.result === 'win' && !known.has(ticketKey(t))),
    seen: [...new Set([...seen, ...settled])],
  }
}

const seenKey = (userId: number) => `sp-seen-wins.${userId}`

export function loadSeen(userId: number): string[] | null {
  try {
    const raw = localStorage.getItem(seenKey(userId))
    if (raw === null) return null
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : null
  } catch {
    return null
  }
}

export function saveSeen(userId: number, keys: string[]): void {
  try { localStorage.setItem(seenKey(userId), JSON.stringify(keys)) } catch { /* unavailable */ }
}
