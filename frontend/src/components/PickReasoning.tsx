import { formatOdds } from '../lib/quotes'
import type { PickReasoning } from '../types'

const pct = (p: number) => `${Math.round(p * 100)}%`

/** The model's case for a pick: its chance vs the market's, the edge, the fair
 *  price, the stake it would make, and the note `rationale.pick_note` wrote. */
export default function PickReasoningPanel({ r }: { r: PickReasoning }) {
  return (
    <div className="sb-why" aria-label="Why this pick">
      <dl className="sb-why-nums">
        <div><dt>Model</dt><dd>{pct(r.model_prob)}</dd></div>
        {r.market_prob !== null && <div><dt>Market</dt><dd>{pct(r.market_prob)}</dd></div>}
        <div><dt>Edge</dt><dd>{r.edge_pct.toFixed(1)}%</dd></div>
        {r.fair_odds !== null && <div><dt>Fair price</dt><dd>{formatOdds(r.fair_odds)}</dd></div>}
        {r.units !== null && <div><dt>Model stake</dt><dd>{r.units.toFixed(2)}u</dd></div>}
      </dl>
      <p className="sb-why-note">{r.note}</p>
    </div>
  )
}
