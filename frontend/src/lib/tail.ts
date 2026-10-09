import { api } from '../api/client'
import { useSlip } from '../stores/slipStore'
import { findQuote, resolveQuoteLabel } from './quotes'
import type { FeedLeg, GameQuote, PropQuote, SlipSelection } from '../types'

type Fetchers = {
  quotes: (gameId: number) => Promise<{ game_id: number; quotes: GameQuote[] }>
  propQuotes: (gameId: number) => Promise<{ game_id: number; quotes: PropQuote[] }>
}
const LIVE: Fetchers = { quotes: api.paper.quotes, propQuotes: api.paper.propQuotes }

/** A friend's bet re-priced for you now (spec §10): today's quote for each
 *  leg, never the price they got. A leg the server refuses keeps its
 *  refusal as `error` -- it shows locked on the slip. */
export async function tailLegs(legs: FeedLeg[], fetchers: Fetchers = LIVE) {
  return Promise.all(legs.map(async fl => {
    const { label, game_label, start_time, home_team, away_team, odds, quoted_line, ...leg } = fl
    const isProp = leg.pick_type === 'prop'
    const quotes = isProp
      ? (await fetchers.propQuotes(leg.game_id)).quotes
      : (await fetchers.quotes(leg.game_id)).quotes
    const q = findQuote(isProp ? [] : (quotes as GameQuote[]), isProp ? (quotes as PropQuote[]) : [], leg)
    const base = { leg, gameLabel: game_label, startTime: start_time, homeTeam: home_team, awayTeam: away_team }
    if (q && q.available) {
      const shown = !isProp && q.pick_type === 'moneyline'
        ? `${('side' in q && q.side === 'HOME') ? home_team : away_team} ML`
        : isProp ? label : resolveQuoteLabel(q, home_team, away_team)
      const selection: SlipSelection = { ...base, label: shown, odds: q.odds, line: q.line }
      return { selection, error: null as string | null }
    }
    const selection: SlipSelection = { ...base, label, odds, line: quoted_line }
    return { selection, error: q && !q.available ? q.message : 'No book is quoting this bet right now.' }
  }))
}

/** Tail a feed bet onto your slip and open it. Returns how many legs are live. */
export async function tailBet(legs: FeedLeg[], fetchers: Fetchers = LIVE): Promise<number> {
  const results = await tailLegs(legs, fetchers)
  const slip = useSlip.getState()
  for (const r of results) {
    slip.add(r.selection)
    if (r.error) useSlip.getState().lock(r.selection.leg, r.error)
  }
  useSlip.getState().setOpen(true)
  return results.filter(r => r.error === null).length
}
