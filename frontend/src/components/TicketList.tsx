import { useState } from 'react'
import TicketCard from './TicketCard'
import { filterSettled, splitTickets, ticketKey, type SettledFilter } from '../lib/bets'
import type { Ticket } from '../types'

const FILTERS: [SettledFilter, string][] = [['all', 'All'], ['won', 'Won'], ['lost', 'Lost']]

/** Open / Settled tabs over one player's tickets: My Bets and a player's page.
 *  Pass ``userId`` only for the viewer's own bets -- it is what puts a Cash out
 *  button on an open ticket. */
export default function TicketList({ tickets, userId, loading = false, loadingText = 'Loading bets…', emptyOpen }: {
  tickets: Ticket[] | undefined; userId?: number; loading?: boolean; loadingText?: string; emptyOpen: string
}) {
  const [tab, setTab] = useState<'open' | 'settled'>('open')
  const [filter, setFilter] = useState<SettledFilter>('all')
  const { open, settled } = splitTickets(tickets ?? [])
  const shown = tab === 'open' ? open : filterSettled(settled, filter)

  return (
    <>
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
      {loading && <p className="sb-empty">{loadingText}</p>}
      {tickets && shown.length === 0 && (
        <p className="sb-empty">{tab === 'open' ? emptyOpen : 'No settled bets here yet.'}</p>
      )}
      {shown.map(t => <TicketCard key={ticketKey(t)} t={t} userId={userId} />)}
    </>
  )
}
