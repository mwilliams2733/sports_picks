import { signedMoney } from '../lib/bets'
import type { PeriodStats, UserStats } from '../types'

const PERIODS: [Exclude<keyof UserStats, 'daily_breakdown'>, string][] = [
  ['today', 'Today'], ['this_week', 'This week'], ['this_month', 'This month'], ['all_time', 'All time']]

const record = (s: PeriodStats) => `${s.wins}-${s.losses}${s.pushes ? `-${s.pushes}` : ''}`
// The endpoint sends percents (36.48, -7.09); a minus is U+2212 like signedMoney.
const signedPct = (n: number) => (n > 0 ? `+${n}%` : n < 0 ? `−${Math.abs(n)}%` : '0%')
const tone = (n: number) => (n > 0 ? 'sb-up' : n < 0 ? 'sb-down' : undefined)

/** A player's results for today, this week, this month and all time
 *  (`/users/{id}/stats`; days are game dates in ET, weeks start Monday). */
export default function PeriodStatsTable({ stats }: { stats: UserStats }) {
  const cashed = stats.all_time.cashed_out
  return (
    <>
      <table className="sb-lead-table" aria-label="Results by period">
        <thead><tr><th>Period</th><th>W-L</th><th>Win %</th><th>Profit</th><th>ROI</th></tr></thead>
        <tbody>
          {PERIODS.map(([key, label]) => {
            const s = stats[key]
            const none = s.total === 0
            return (
              <tr key={key} aria-label={label}>
                <td>{label}</td>
                <td>{none ? '—' : record(s)}</td>
                <td>{none || s.wins + s.losses === 0 ? '—' : `${s.win_rate}%`}</td>
                <td className={none ? undefined : tone(s.profit)}>{none ? '—' : signedMoney(s.profit)}</td>
                <td className={none ? undefined : tone(s.roi)}>{none ? '—' : signedPct(s.roi)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {cashed > 0 && (
        <p className="sb-note">Profit and ROI include {cashed} cashed-out {cashed === 1 ? 'bet' : 'bets'}; W-L does not.</p>
      )}
    </>
  )
}
