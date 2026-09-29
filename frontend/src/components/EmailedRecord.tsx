import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useEmailedRecord, type EmailedBy, type EmailedKind } from '../hooks/useEmailedRecord'
import type { EmailedSummary } from '../types'

const pct = (x: number | null) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)
const units = (x: number) => `${x >= 0 ? '+' : ''}${x.toFixed(2)}u`
// No Stars grouping: stars are hidden until a definition is validated
// (owner decision 2026-09-28). The API still serves by=stars for that work.
const BYS: { key: EmailedBy; label: string }[] = [
  { key: 'week', label: 'Week' }, { key: 'month', label: 'Month' },
]

function groupLabel(by: EmailedBy, label: string) {
  return by === 'week' ? `Week of ${label}` : label
}

export default function EmailedRecord() {
  const [params, setParams] = useSearchParams()
  const kind = (params.get('kind') === 'prop' ? 'prop' : 'game') as EmailedKind
  const by = (params.get('by') === 'month' ? 'month' : 'week') as EmailedBy
  const set = (k: string, v: string) => { const next = new URLSearchParams(params); next.set(k, v); setParams(next) }
  const { groups, trend } = useEmailedRecord(kind, by)

  if (groups.error || trend.error) {
    return <div className="empty-state"><div className="empty-state-title text-red">Error: {((groups.error || trend.error) as Error).message}</div></div>
  }
  if (groups.isLoading || trend.isLoading) return <div className="loading"><div className="spinner" /> Loading...</div>

  const total = groups.data!.total
  const rows = groups.data!.groups
  const points = trend.data!.points

  return (
    <div>
      <div className="tab-group" style={{ marginBottom: '1rem' }}>
        {(['game', 'prop'] as EmailedKind[]).map(k => (
          <button key={k} className={`tab${kind === k ? ' active' : ''}`} aria-pressed={kind === k} onClick={() => set('kind', k)}>
            {k === 'game' ? 'Game picks' : 'Props'}
          </button>
        ))}
      </div>

      {total.n === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No emailed picks have settled yet</div>
          <div>{total.pending} pending. Recording began 2026-09-28.</div>
        </div>
      ) : (
        <>
          <div className="card-grid">
            <div className="stat-card"><div className="stat-label">Win rate</div>
              <div className="stat-value">{pct(total.win_rate)}</div>
              <div className="stat-sub">90% range {pct(total.range_low)}–{pct(total.range_high)}</div></div>
            <div className="stat-card"><div className="stat-label">Needed to break even</div>
              <div className="stat-value">{pct(total.break_even)}</div></div>
            <div className="stat-card"><div className="stat-label">ROI</div>
              <div className="stat-value">{pct(total.roi)}</div>
              <div className="stat-sub">{units(total.profit)} on {total.n} settled</div></div>
            <div className="stat-card"><div className="stat-label">Record</div>
              <div className="stat-value">{total.wins}-{total.losses}-{total.pushes}</div>
              <div className="stat-sub">{total.pending} pending</div></div>
          </div>

          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="section-header">Cumulative units</div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => units(Number(v))} />
                <Line type="monotone" dataKey="units" stroke="var(--accent)" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
            <div className="stat-sub">Max drawdown {trend.data!.max_drawdown.toFixed(2)}u · longest losing streak {trend.data!.longest_losing_streak}</div>
          </div>
        </>
      )}

      <div className="tab-group" style={{ margin: '1rem 0 0.5rem' }}>
        {BYS.map(b => (
          <button key={b.key} className={`tab${by === b.key ? ' active' : ''}`} aria-pressed={by === b.key} onClick={() => set('by', b.key)}>{b.label}</button>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Period</th><th>W-L-P</th><th>Win rate</th><th>90% range</th><th>Break-even</th><th>Units</th><th>ROI</th><th>Pending</th></tr></thead>
          <tbody>
            {rows.map((g: EmailedSummary) => (
              <tr key={g.label}>
                <td>{groupLabel(by, g.label)}</td>
                <td>{g.wins}-{g.losses}-{g.pushes}</td>
                <td data-testid={`winrate-${g.label}`} className={g.verdict ? `verdict-${g.verdict}` : ''}>{pct(g.win_rate)}</td>
                <td>{pct(g.range_low)}–{pct(g.range_high)}</td>
                <td data-testid={`breakeven-${g.label}`}>{pct(g.break_even)}</td>
                <td>{units(g.profit)}</td>
                <td>{pct(g.roi)}</td>
                <td>{g.pending}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="stat-sub">Colored only when the whole 90% range is above (green) or below (red) break-even; otherwise the sample cannot yet tell.</p>
    </div>
  )
}
