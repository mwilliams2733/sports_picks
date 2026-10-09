import TicketList from '../components/TicketList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { signedMoney } from '../lib/bets'
import { formatMoney } from '../lib/board'

export default function MyBets() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)

  if (!me) {
    return <div className="sb-bets"><p className="sb-empty">Choose a player in the top bar, or join the league, to see your bets.</p></div>
  }
  const data = bets.data
  const pl = data?.summary.today_pl ?? 0

  return (
    <div className="sb-bets">
      {data && (
        <dl className="sb-money" aria-label="Your money">
          <div><dt>Available</dt><dd>{formatMoney(data.summary.available)}</dd></div>
          <div><dt>Settled balance</dt><dd>{formatMoney(data.summary.balance)}</dd></div>
          <div><dt>Open stakes</dt><dd>{formatMoney(data.summary.open_stakes)}</dd></div>
          <div><dt>Today's P/L</dt>
            <dd className={pl > 0 ? 'sb-up' : pl < 0 ? 'sb-down' : undefined}>{signedMoney(pl)}</dd></div>
        </dl>
      )}
      {bets.isError && <div role="alert" className="sb-offline">Couldn't load your bets — retrying.</div>}
      <TicketList tickets={data?.tickets} userId={me.id} loading={bets.isLoading}
        loadingText="Loading your bets…" emptyOpen="No open bets — tap a price in the Lobby to start." />
    </div>
  )
}
