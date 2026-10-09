import { gameLine, isLive, legLabel, legStatus, legTint, signedMoney, ticketCode, ticketType } from '../lib/bets'
import { formatMoney } from '../lib/board'
import CashOutButton from './CashOutButton'
import { formatOdds } from '../lib/quotes'
import type { Ticket } from '../types'

const RESULT: Record<string, string> = { win: 'Won', loss: 'Lost', push: 'Push', cashed_out: 'Cashed out' }
const TINT: Record<string, string> = { winning: 'Winning', losing: 'Losing', even: 'Even' }

export default function TicketCard({ t, userId }: { t: Ticket; userId?: number }) {
  const edge = t.result === 'win' ? ' sb-bet-won' : t.result === 'loss' ? ' sb-bet-lost' : ''
  const live = t.result === null && t.legs.some(l => isLive(l.game))
  return (
    <article className={`sb-bet${edge}`} aria-label={`Bet ${ticketCode(t)}`}>
      <header className="sb-bet-head">
        <span className="sb-bet-type">{ticketType(t)}</span>
        <span>{ticketCode(t)}</span>
        {live && <span className="sb-live">LIVE</span>}
        {t.result === 'cashed_out' && <span className="sb-cashed">CASHED OUT</span>}
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
            {(() => {
              const tint = legTint(l)
              return tint && <span className={`sb-tint sb-tint-${tint}`}>{TINT[tint]}</span>
            })()}
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
      {t.result === null && userId !== undefined && <CashOutButton t={t} userId={userId} />}
    </article>
  )
}
