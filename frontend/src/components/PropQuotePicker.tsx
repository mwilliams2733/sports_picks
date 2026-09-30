import { useState } from 'react'
import type { AvailablePropQuote, PropQuote } from '../types'
import { formatOdds } from '../lib/quotes'

interface Props {
  quotes: PropQuote[]
  selected: AvailablePropQuote | null
  onSelect: (quote: AvailablePropQuote) => void
}

const rowKey = (q: PropQuote) => `${q.prop_player}|${q.prop_market}|${q.line}`

/** The first unavailable side's reason, or null. */
function refusal(sides: PropQuote[]): string | null {
  for (const q of sides) if (!q.available) return q.message
  return null
}

export default function PropQuotePicker({ quotes, selected, onSelect }: Props) {
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const rows = new Map<string, PropQuote[]>()
  for (const q of quotes) {
    if (needle && !`${q.prop_player} ${q.market_label}`.toLowerCase().includes(needle)) continue
    rows.set(rowKey(q), [...(rows.get(rowKey(q)) ?? []), q])
  }
  return (
    <div>
      <input className="input" aria-label="Search props" placeholder="Search by player or market..."
        value={search} onChange={e => setSearch(e.target.value)} />
      <div className="prop-quote-list">
        {[...rows.entries()].slice(0, 40).map(([key, sides]) => (
          <div key={key} className="prop-quote-row">
            <span className="font-medium">{sides[0].prop_player}</span>
            <span className="text-muted"> {sides[0].market_label}</span>
            <div className="quote-sides">
              {sides.map(q => (
                <button
                  key={q.outcome}
                  type="button"
                  className={`quote-side${selected && rowKey(selected) === key && selected.outcome === q.outcome ? ' selected' : ''}`}
                  disabled={!q.available}
                  title={q.available ? undefined : q.message}
                  onClick={() => { if (q.available) onSelect(q) }}
                >
                  <span>{q.outcome} {q.line}</span>{' '}
                  <span className="mono">{q.available ? formatOdds(q.odds) : '—'}</span>
                </button>
              ))}
            </div>
            {refusal(sides) && <div className="quote-reason">{refusal(sides)}</div>}
          </div>
        ))}
        {rows.size === 0 && <div className="text-muted">No props quoted for this game.</div>}
      </div>
    </div>
  )
}
