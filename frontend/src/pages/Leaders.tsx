import FeedList from '../components/FeedList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useFeed } from '../hooks/useFeed'
import { useRankings } from '../hooks/useRankings'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { signedMoney } from '../lib/bets'
import type { LeaderboardRow } from '../types'

const MIN_RANKED = 10            // backend MIN_RANKED_BETS
const TOP = 10                   // rows shown before "your position" is pinned

function Row({ r, rank, streak }: { r: LeaderboardRow; rank: number | null; streak: number }) {
  return (
    <tr aria-label={r.name} className={r.is_model ? 'sb-lead-model' : undefined}>
      <td className="sb-lead-rank">{rank ?? '—'}</td>
      <td>
        <span className="sb-avatar" aria-hidden="true">{r.name.charAt(0).toUpperCase()}</span>
        {/* The Model has no player page (id null): its record is Track record. */}
        <Link className="sb-lead-name" to={r.is_model || r.id === null ? '/track-record' : `/players/${r.id}`}>{r.name}</Link>
      </td>
      <td className={r.profit > 0 ? 'sb-up' : r.profit < 0 ? 'sb-down' : undefined}>{signedMoney(r.profit)}</td>
      <td>{r.roi === null ? '—' : `${(r.roi * 100).toFixed(1)}%`}</td>
      <td>{r.wins}-{r.losses}{r.pushes ? `-${r.pushes}` : ''}</td>
      <td>{r.ranked ? (streak >= 2 ? `🔥 ${streak}` : '') : `Unranked (${r.n}/${MIN_RANKED})`}</td>
    </tr>
  )
}

export default function Leaders() {
  const board = useRankings()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const feed = useFeed(50)
  const me = useCurrentPlayer()
  const rows = board.data ?? []
  const streakOf = (id: number | null) => {
    const u = id === null ? undefined : users.data?.find(x => x.id === id)
    return u && u.streak_type === 'win' ? u.current_streak : 0
  }
  let ranked = 0
  const ranks = rows.map(r => (r.ranked ? ++ranked : null))
  const myIndex = me ? rows.findIndex(r => r.id === me.id) : -1

  return (
    <div className="sb-leaders">
      <h1 className="sb-page-title">Leaders</h1>
      <table className="sb-lead-table">
        <thead><tr><th>#</th><th>Player</th><th>Profit</th><th>ROI</th><th>W-L</th><th /></tr></thead>
        <tbody>
          {rows.slice(0, TOP).map((r, i) => <Row key={`${r.id}-${r.name}`} r={r} rank={ranks[i]} streak={streakOf(r.id)} />)}
        </tbody>
      </table>
      {myIndex >= TOP && (
        <table className="sb-lead-table sb-lead-pinned" aria-label="Your position">
          <tbody><Row r={rows[myIndex]} rank={ranks[myIndex]} streak={streakOf(rows[myIndex].id)} /></tbody>
        </table>
      )}
      <h2 className="sb-day">League feed</h2>
      <FeedList items={feed.data ?? []} meId={me?.id} />
    </div>
  )
}
