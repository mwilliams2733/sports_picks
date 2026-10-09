import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import PeriodStatsTable from '../components/PeriodStatsTable'
import TicketList from '../components/TicketList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { usePlayerStats } from '../hooks/usePlayerStats'
import { signedMoney } from '../lib/bets'
import { formatMoney } from '../lib/board'
import type { UserProfile } from '../types'

function streakText(u: UserProfile): string {
  if (!u.current_streak || u.streak_type === 'none') return '—'
  return `${u.streak_type === 'win' ? 'W' : 'L'}${u.current_streak}`
}

function PlayerBody({ player, isMe }: { player: UserProfile; isMe: boolean }) {
  const stats = usePlayerStats(player.id)
  const bets = useMyBets(player.id)
  return (
    <div className="sb-bets sb-player">
      <h1 className="sb-page-title">
        <span className="sb-avatar" aria-hidden="true">{player.name.charAt(0).toUpperCase()}</span>{player.name}
      </h1>
      <dl className="sb-money" aria-label={`${player.name}'s money`}>
        <div><dt>Balance</dt><dd>{formatMoney(player.current_balance)}</dd></div>
        <div><dt>Profit</dt>
          <dd className={player.profit > 0 ? 'sb-up' : player.profit < 0 ? 'sb-down' : undefined}>{signedMoney(player.profit)}</dd></div>
        <div><dt>Streak</dt><dd>{streakText(player)}</dd></div>
        <div><dt>Best win streak</dt><dd>{player.best_streak ? `W${player.best_streak}` : '—'}</dd></div>
      </dl>
      <p className="sb-note">Leaders ranks single bets only; these figures include parlays.</p>
      {isMe && (
        <p className="sb-note">This is you — cash out and track your open bets in <Link className="sb-link" to="/bets">My Bets</Link>.</p>
      )}
      <h2 className="sb-day">Results</h2>
      {stats.isError && <div role="alert" className="sb-offline">Couldn't load results — retrying.</div>}
      {stats.data && <PeriodStatsTable stats={stats.data} />}
      <h2 className="sb-day">Bets</h2>
      {bets.isError && <div role="alert" className="sb-offline">Couldn't load bets — retrying.</div>}
      {/* No userId: another player's bets are visible to everyone (spec §10) but only the owner cashes out. */}
      <TicketList tickets={bets.data?.tickets} loading={bets.isLoading} emptyOpen={`${player.name} has no open bets.`} />
    </div>
  )
}

/** A player's page (`/players/:id`), opened from Leaders. */
export default function PlayerView() {
  const { id } = useParams()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const me = useCurrentPlayer()
  const playerId = /^\d+$/.test(id ?? '') ? Number(id) : undefined
  const player = playerId === undefined ? undefined : users.data?.find(u => u.id === playerId)

  if (users.isError && !users.data) {
    return <div className="sb-bets"><div role="alert" className="sb-offline">Couldn't load players — retrying.</div></div>
  }
  if (users.isLoading) return <div className="sb-bets"><p className="sb-empty">Loading…</p></div>
  if (!player) {
    return (
      <div className="sb-bets">
        <p className="sb-empty">No such player. <Link className="sb-link" to="/leaders">Back to Leaders</Link></p>
      </div>
    )
  }
  // Keyed by id so moving between two players' pages resets the tabs and filter.
  return <PlayerBody key={player.id} player={player} isMe={me?.id === player.id} />
}
