import type { AvailableGameQuote, GamePickType, GameQuote, GameSide } from '../types'
import { formatOdds } from '../lib/quotes'

interface Props {
  quotes: GameQuote[]
  pickType: GamePickType
  homeName: string
  awayName: string
  selected: GameSide | null
  onSelect: (quote: AvailableGameQuote) => void
}

function sideLabel(q: GameQuote, homeName: string, awayName: string): string {
  const team = q.side === 'HOME' ? homeName : q.side === 'AWAY' ? awayName : q.side
  if (!q.available || q.line === null) return team
  if (q.pick_type === 'spread') return `${team} ${q.line > 0 ? '+' : ''}${q.line}`
  return `${team} ${q.line}`
}

export default function QuotePicker({ quotes, pickType, homeName, awayName, selected, onSelect }: Props) {
  const sides = quotes.filter(q => q.pick_type === pickType)
  return (
    <div className="quote-sides">
      {sides.map(q => (
        <div key={q.side} className="quote-side-wrap">
          <button
            type="button"
            className={`quote-side${selected === q.side ? ' selected' : ''}`}
            disabled={!q.available}
            aria-pressed={selected === q.side}
            onClick={() => { if (q.available) onSelect(q) }}
          >
            <span>{sideLabel(q, homeName, awayName)}</span>{' '}
            <span className="mono">{q.available ? formatOdds(q.odds) : '—'}</span>
          </button>
          {!q.available && <div className="quote-reason">{q.message}</div>}
        </div>
      ))}
    </div>
  )
}
