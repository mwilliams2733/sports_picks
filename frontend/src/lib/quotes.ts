import type { AvailableGameQuote, AvailablePropQuote, BetLeg, GameQuote, PropQuote } from '../types'

export function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`
}

//: Shared with legFromPick's prop branch and propOutcomeLabel below -- one
//: pattern for "what does an Over/Under prop label look like".
const PROP_OUTCOME_RE = /\b(Over|Under)\s+(\d+(?:\.\d+)?)/

/** A model pick (Today's Picks, Player Props) as a bet request, or null when
 *  the label can't be mapped to a side the server prices. */
export function legFromPick(pickType: string, pickValue: string, gameId: number,
  propMarket?: string, propPlayer?: string): BetLeg | null {
  if (pickType === 'moneyline' || pickType === 'spread') {
    const side = /\bHOME\b/i.test(pickValue) ? 'HOME' : /\bAWAY\b/i.test(pickValue) ? 'AWAY' : null
    return side ? { game_id: gameId, pick_type: pickType, side } : null
  }
  if (pickType === 'over_under') {
    const side = /\bOver\b/.test(pickValue) ? 'Over' : /\bUnder\b/.test(pickValue) ? 'Under' : null
    return side ? { game_id: gameId, pick_type: 'over_under', side } : null
  }
  if (pickType === 'prop' && propMarket && propPlayer) {
    const m = pickValue.match(PROP_OUTCOME_RE)
    if (!m) return null
    return { game_id: gameId, pick_type: 'prop', prop_player: propPlayer, prop_market: propMarket,
      outcome: m[1] as 'Over' | 'Under', line: Number(m[2]) }
  }
  return null
}

/** The bare "Over 225.5" a prop's outcome/line reduce to, stripped of the
 *  player name and market label a stored pick_value ("Jalen Hurts Over
 *  225.5 Pass Yards") otherwise carries -- so a shown label and a charged
 *  label can be compared apples to apples. Falls back to the raw string
 *  when it doesn't parse (should not happen for a gradeable prop). */
export function propOutcomeLabel(pickValue: string): string {
  const m = pickValue.match(PROP_OUTCOME_RE)
  return m ? `${m[1]} ${m[2]}` : pickValue
}

/** Replaces the HOME/AWAY tokens a game quote's or a stored pick's
 *  pick_value carries ("AWAY +2.5", "HOME ML") with real team names, the
 *  same substitution backend/api/picks.py's `_resolve_pick_value` does for
 *  the same strings server-side. Over/Under labels have no HOME/AWAY token
 *  and pass through unchanged, as does any prop label. */
export function resolveLabel(label: string, homeTeam: string, awayTeam: string): string {
  return label
    .replace('HOME ML', `${homeTeam} ML`)
    .replace('AWAY ML', `${awayTeam} ML`)
    .replace('HOME ', `${homeTeam} `)
    .replace('AWAY ', `${awayTeam} `)
}

/** A quote's shown side, team-resolved: "BOS +2.5" (spread), "BOS" (moneyline
 *  -- no numeric line to show), "Over 7.5" (total), "Over 225.5" (prop). The
 *  team-name lookup for game markets; shared by QuotePicker (which needs it
 *  disabled or not) and BetModal (which needs it for display and for the
 *  price-move note). */
export function resolveQuoteLabel(quote: GameQuote | PropQuote, homeTeam: string, awayTeam: string): string {
  if ('side' in quote) {
    const team = quote.side === 'HOME' ? homeTeam : quote.side === 'AWAY' ? awayTeam : quote.side
    if (!quote.available || quote.line === null) return team
    if (quote.pick_type === 'spread') return `${team} ${quote.line > 0 ? '+' : ''}${quote.line}`
    return `${team} ${quote.line}`
  }
  return `${quote.outcome} ${quote.line}`
}

/** A quote as a bet request. Legs are keyed by (game_id, pick_type) for game
 *  markets and (game_id, prop_player, prop_market) for props, not by
 *  game_id alone -- same-game parlays are allowed, and the server refuses
 *  two legs on the same market with a 400. */
export function legFromQuote(gameId: number, q: AvailableGameQuote | AvailablePropQuote): BetLeg {
  if ('side' in q) return { game_id: gameId, pick_type: q.pick_type, side: q.side }
  return { game_id: gameId, pick_type: 'prop', prop_player: q.prop_player, prop_market: q.prop_market,
    outcome: q.outcome, line: q.line }
}

export function findQuote(game: GameQuote[], props: PropQuote[], leg: BetLeg): GameQuote | PropQuote | undefined {
  if (leg.pick_type === 'prop') {
    return props.find(q => q.prop_player === leg.prop_player && q.prop_market === leg.prop_market
      && q.outcome === leg.outcome && q.line === leg.line)
  }
  return game.find(q => q.pick_type === leg.pick_type && q.side === leg.side)
}

/** What the player saw ("shown") vs what the server actually charged
 *  ("charged") -- both a label and a price, since either can move between
 *  the quote a player looked at and the one the server re-prices on
 *  placement (Review Focus: a line move used to be charged silently). Notes
 *  when EITHER the label or the price differs; says nothing extra when
 *  neither did. */
export function priceMoveNote(
  shownValue: string, shownOdds: number,
  chargedValue: string, chargedOdds: number,
): string {
  const placed = `Placed at ${chargedValue} ${formatOdds(chargedOdds)}`
  if (shownValue === chargedValue && shownOdds === chargedOdds) return placed
  return `${placed} — was ${shownValue} ${formatOdds(shownOdds)} when you looked`
}

/** The same formula as backend pricing.combine; only an estimate -- the server
 *  prices every leg again when the parlay is placed. */
export function parlayEstimate(odds: number[]): { american: number; decimal: number } | null {
  if (odds.length < 2) return null
  const decimal = odds.reduce((acc, o) => acc * (o < 0 ? 1 + 100 / Math.abs(o) : 1 + o / 100), 1)
  const american = decimal >= 2 ? Math.round((decimal - 1) * 100) : Math.round(-100 / (decimal - 1))
  return { american, decimal }
}

/** A leg's market -- (game_id, pick_type) for a game leg, (game_id,
 *  prop_player, prop_market) for a prop leg. The server refuses two legs on
 *  the same market with a 400, so the parlay builder keys its slip by this,
 *  not by game_id alone -- same-game parlays on different markets are fine. */
export function marketKey(leg: BetLeg): string {
  return leg.pick_type === 'prop'
    ? `${leg.game_id}|prop|${leg.prop_player}|${leg.prop_market}`
    : `${leg.game_id}|${leg.pick_type}`
}

/** Adds a leg to a parlay slip, replacing any existing leg on the same
 *  market rather than appending a second one. */
export function addOrReplaceLeg<T extends { leg: BetLeg }>(legs: T[], newLeg: T): T[] {
  const key = marketKey(newLeg.leg)
  return [...legs.filter(l => marketKey(l.leg) !== key), newLeg]
}

export function ageLabel(quotedAt: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(quotedAt).getTime()) / 60000)
  if (minutes < 1) return 'Prices fetched just now'
  if (minutes < 60) return `Prices fetched ${minutes}m ago`
  return `Prices fetched ${Math.floor(minutes / 60)}h ago`
}

// Note Math.round rounds .5 up while Python's round rounds half to even;
// the estimate can differ by 1 on an exact half, which is why it is
// labelled an estimate.
