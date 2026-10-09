import { gameLine, legLabel, legStatus, signedMoney, ticketCode, ticketType } from '../lib/bets'
import { formatMoney } from '../lib/board'
import { formatOdds } from '../lib/quotes'
import type { Ticket } from '../types'

const RESULT: Record<string, string> = { win: 'Won', loss: 'Lost', push: 'Push' }

export default function TicketCard({ t }: { t: Ticket }) {
  const edge = t.result === 'win' ? ' sb-bet-won' : t.result === 'loss' ? ' sb-bet-lost' : ''
  return (
    <article className={`sb-bet${edge}`} aria-label={`Bet ${ticketCode(t)}`}>
      <header className="sb-bet-head">
        <span className="sb-bet-type">{ticketType(t)}</span>
        <span>{ticketCode(t)}</span>
        <span className="sb-bet-time">
          {new Date(t.created_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}
        </span>
      </header>
      <ul className="sb-bet-legs">
        {t.legs.map((l, i) => (
          <li key={i} className="sb-bet-leg">
            <span className={`sb-dot sb-dot-${legStatus(l)}`} title={legStatus(l)} />
            <div>
              <strong>{legLabel(l)}</strong>
              <small>{gameLine(l.game)}</small>
            </div>
            <span className="sb-bet-odds">{formatOdds(l.odds)}</span>
          </li>
        ))}
      </ul>
      <footer className="sb-bet-foot">
        <span>Stake {formatMoney(t.stake)}{t.kind === 'parlay' && ` at ${formatOdds(t.odds)}`}</span>
        {t.result === null
          ? <strong>To win {formatMoney(t.to_win)}</strong>
          : <strong>{RESULT[t.result] ?? t.result} {signedMoney(t.payout ?? 0)}</strong>}
      </footer>
    </article>
  )
}
