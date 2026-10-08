import OddsTile from './OddsTile'
import { modelPickQuote, modelPickSelection, tileTop } from '../lib/board'
import { sameLeg } from '../lib/slip'
import { useSlip } from '../stores/slipStore'
import { formatOdds, resolveQuoteLabel } from '../lib/quotes'
import type { BoardGame, GameQuote, SlipSelection } from '../types'

function StripTile({ g, q, label, offline, onPick }: {
  g: BoardGame; q: GameQuote; label: string; offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const sel = q.available ? modelPickSelection(g, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  return <OddsTile label={label} top={q.available ? tileTop(q) : null}
    price={q.available ? q.odds : null} line={q.available ? q.line : null}
    lockedReason={q.available ? undefined : q.message} offline={offline} selected={selected}
    onSelect={() => { if (sel) onPick(sel) }} />
}

export default function ModelPicksStrip({ games, offline, onPick }: {
  games: BoardGame[]; offline: boolean; onPick: (s: SlipSelection) => void
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
            <StripTile g={g} q={q} label={label} offline={offline} onPick={onPick} />
          </div>
        )
      })}
    </section>
  )
}
