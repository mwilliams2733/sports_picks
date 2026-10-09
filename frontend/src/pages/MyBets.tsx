import { useState } from 'react'
import TicketCard from '../components/TicketCard'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { filterSettled, signedMoney, splitTickets, ticketKey, type SettledFilter } from '../lib/bets'
import { formatMoney } from '../lib/board'

const FILTERS: [SettledFilter, string][] = [['all', 'All'], ['won', 'Won'], ['lost', 'Lost']]

export default function MyBets() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)
  const [tab, setTab] = useState<'open' | 'settled'>('open')
  const [filter, setFilter] = useState<SettledFilter>('all')

  if (!me) {
    return <div className="sb-bets"><p className="sb-empty">Choose a player in the top bar, or join the league, to see your bets.</p></div>
  }
  const data = bets.data
  const { open, settled } = splitTickets(data?.tickets ?? [])
  const shown = tab === 'open' ? open : filterSettled(settled, filter)
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
      <div className="sb-sport-tabs" role="tablist" aria-label="Bets">
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'open'} onClick={() => setTab('open')}>
          Open ({open.length})
        </button>
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'settled'} onClick={() => setTab('settled')}>
          Settled
        </button>
      </div>
      {tab === 'settled' && (
        <div className="sb-filter" role="group" aria-label="Filter settled bets">
          {FILTERS.map(([f, label]) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{label}</button>
          ))}
        </div>
      )}
      {bets.isLoading && <p className="sb-empty">Loading your bets…</p>}
      {data && shown.length === 0 && (
        <p className="sb-empty">
          {tab === 'open' ? 'No open bets — tap a price in the Lobby to start.' : 'No settled bets here yet.'}
        </p>
      )}
      {shown.map(t => <TicketCard key={ticketKey(t)} t={t} userId={me.id} />)}
    </div>
  )
}
