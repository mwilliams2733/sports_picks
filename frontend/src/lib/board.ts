import type { AvailableGameQuote, AvailablePropQuote, BoardGame, GamePickType, GameQuote,
  GameSide, PropQuote, SlipSelection } from '../types'
import { legFromPick, legFromQuote, resolveLabel, resolveQuoteLabel } from './quotes'
import { lineFromPick } from './slip'

const ET = 'America/New_York'

/** Today's date in ET as YYYY-MM-DD -- the convention Game.date is stored in. */
export function etToday(now: Date): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ET }).format(now)
}

/** "2026-10-11" -> "Sun Oct 11". Noon local keeps the date from shifting. */
export function formatDay(date: string): string {
  return new Date(`${date}T12:00:00`)
    .toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
    .replace(',', '')
}

export function startLabel(iso: string | null): string {
  if (!iso) return 'TBD'
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

export function formatMoney(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export function groupByDay(games: BoardGame[], today: string) {
  const days: { date: string; label: string; games: BoardGame[] }[] = []
  for (const g of games) {
    let day = days.find(d => d.date === g.date)
    if (!day) {
      day = { date: g.date, label: g.date === today ? 'Today' : formatDay(g.date), games: [] }
      days.push(day)
    }
    day.games.push(g)
  }
  return days
}

export function sportTabs(games: BoardGame[]): string[] {
  return [...new Set(games.map(g => g.sport))]
}

export function findGameQuote(quotes: GameQuote[], pickType: GamePickType, side: GameSide): GameQuote | undefined {
  return quotes.find(q => q.pick_type === pickType && q.side === side)
}

/** The small line above the price on a tile: "+3", "O 47.5", or none (ML). */
export function tileTop(q: AvailableGameQuote): string | null {
  if (q.line === null || q.pick_type === 'moneyline') return null
  if (q.pick_type === 'over_under') return `${q.side === 'Over' ? 'O' : 'U'} ${q.line}`
  return q.line > 0 ? `+${q.line}` : `${q.line}`
}

export type PriceMove = 'better' | 'worse' | 'moved' | null
type Price = { odds: number; line: number | null }

/** How a tile's price changed between two refetches. Higher American odds
 *  always pay more, so they are better for the bettor; a line change has no
 *  single direction across sides, so it is just "moved". */
export function priceMove(prev: Price | null, next: Price | null): PriceMove {
  if (!prev || !next) return null
  if (prev.line !== next.line) return 'moved'
  if (next.odds > prev.odds) return 'better'
  if (next.odds < prev.odds) return 'worse'
  return null
}

export function gameLabelOf(game: BoardGame): string {
  return `${game.away_team} @ ${game.home_team}`
}

export function gameSelection(game: BoardGame, q: AvailableGameQuote): SlipSelection {
  const team = q.side === 'HOME' ? game.home_team : game.away_team
  return {
    leg: legFromQuote(game.id, q),
    label: q.pick_type === 'moneyline' ? `${team} ML` : resolveQuoteLabel(q, game.home_team, game.away_team),
    gameLabel: gameLabelOf(game), startTime: game.start_time, odds: q.odds, line: q.line,
    homeTeam: game.home_team, awayTeam: game.away_team,
  }
}

export function propSelection(game: BoardGame, q: AvailablePropQuote): SlipSelection {
  return {
    leg: legFromQuote(game.id, q), label: `${q.prop_player} ${q.outcome} ${q.line} ${q.market_label}`,
    gameLabel: gameLabelOf(game), startTime: game.start_time, odds: q.odds, line: q.line,
    homeTeam: game.home_team, awayTeam: game.away_team,
  }
}

/** A model pick on the slip at the label and price the model evaluated (its
 *  edge is only true there); the server's price_moved refusal tells the
 *  player if the live price `q` has moved since. */
export function modelPickSelection(game: BoardGame, q: AvailableGameQuote): SlipSelection {
  const mp = game.model_pick
  if (!mp) return gameSelection(game, q)
  return { ...gameSelection(game, q), label: resolveLabel(mp.pick_value, game.home_team, game.away_team),
    odds: mp.odds ?? q.odds, line: lineFromPick(mp.pick_type, mp.pick_value) }
}

/** The live quote for a game's model pick -- available or refused -- or null
 *  when there is no pick or its stored label maps to no priced side. */
export function modelPickQuote(game: BoardGame): GameQuote | null {
  const mp = game.model_pick
  if (!mp) return null
  const leg = legFromPick(mp.pick_type, mp.pick_value, game.id)
  if (!leg || leg.pick_type === 'prop') return null
  return findGameQuote(game.quotes, leg.pick_type, leg.side) ?? null
}

export interface PropRow { player: string; line: number; over?: PropQuote; under?: PropQuote }

export function groupProps(quotes: PropQuote[]): [string, PropRow[]][] {
  const markets = new Map<string, PropRow[]>()
  for (const q of quotes) {
    const rows = markets.get(q.market_label) ?? []
    let row = rows.find(r => r.player === q.prop_player && r.line === q.line)
    if (!row) {
      row = { player: q.prop_player, line: q.line, over: undefined, under: undefined }
      rows.push(row)
    }
    if (q.outcome === 'Over') row.over = q
    else row.under = q
    markets.set(q.market_label, rows)
  }
  return [...markets.entries()]
}
