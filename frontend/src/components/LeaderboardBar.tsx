import type { KeyboardEvent } from 'react'
import type { LeaderboardRow } from '../types'

const pct = (x: number | null, signed = false) =>
  x == null ? '—' : `${signed && x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

interface Props {
  rows: LeaderboardRow[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export default function LeaderboardBar({ rows, selectedId, onSelect }: Props) {
  let rank = 0
  return (
    <div className="leaderboard-bar">
      {rows.map(r => {
        const label = r.ranked ? `#${++rank}` : 'needs 10 bets'
        const selectable = !r.is_model && r.id != null
        return (
          <div
            key={r.is_model ? 'model' : r.id}
            className={`leaderboard-entry${r.is_model ? ' model' : ''}${selectedId != null && r.id === selectedId ? ' selected' : ''}`}
            {...(selectable ? {
              role: 'button', tabIndex: 0, 'aria-label': `Select ${r.name}`,
              onClick: () => onSelect(r.id as number),
              onKeyDown: (e: KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.id as number) }
              },
            } : {})}
          >
            <span className="leaderboard-rank">{label}</span>
            <span className="leaderboard-name">{r.name}</span>
            <span className="leaderboard-roi" title="Adjusted ROI: pulled toward about -4.5% until the record is long"
              style={{ color: (r.shrunk_roi ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {pct(r.shrunk_roi, true)}
            </span>
            <span className="leaderboard-raw">raw {pct(r.roi, true)}</span>
            <span className="leaderboard-record">{r.wins}-{r.losses}-{r.pushes}</span>
            <span className="leaderboard-winrate">{pct(r.win_rate)}</span>
            {r.pending > 0 && <span className="leaderboard-pending">{r.pending} pending</span>}
          </div>
        )
      })}
      <div className="leaderboard-caption">Ranked by adjusted ROI · single bets only · 10 settled bets to be ranked</div>
    </div>
  )
}
