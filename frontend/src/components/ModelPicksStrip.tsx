import OddsTile from './OddsTile'
import { modelPickQuote, modelPickTarget, tileTop } from '../lib/board'
import { formatOdds, resolveQuoteLabel } from '../lib/quotes'
import type { BetTarget, BoardGame } from '../types'

export default function ModelPicksStrip({ games, offline, onPick }: {
  games: BoardGame[]; offline: boolean; onPick: (t: BetTarget) => void
}) {
  const cards = games.flatMap(g => {
    const q = modelPickQuote(g)
    return q && g.model_pick ? [{ g, q, mp: g.model_pick }] : []
  })
  if (cards.length === 0) return null
  return (
    <section className="sb-strip" aria-label="Metric Edge model picks">
      {cards.map(({ g, q, mp }) => {
        const label = q.available ? resolveQuoteLabel(q, g.home_team, g.away_team) : `${g.away_team} @ ${g.home_team}`
        return (
          <div className="sb-strip-card" key={g.id}>
            {/* The edge was computed at the model's price, which the live tile may no longer show. */}
            <small>MODEL PICK · Model edge {mp.edge_pct.toFixed(1)}%{mp.odds !== null && ` at ${formatOdds(mp.odds)}`}</small>
            <strong>{label}</strong>
            <OddsTile label={label} top={q.available ? tileTop(q) : null}
              price={q.available ? q.odds : null} line={q.available ? q.line : null}
              lockedReason={q.available ? undefined : q.message} offline={offline}
              onSelect={() => { if (q.available) onPick(modelPickTarget(g, q)) }} />
          </div>
        )
      })}
    </section>
  )
}
