import { useSlip } from '../stores/slipStore'
import { formatOdds } from '../lib/quotes'
import { formatMoney } from '../lib/board'
import type { Receipt } from '../types'

export default function BetReceipt({ receipt, remaining }: { receipt: Receipt; remaining: number }) {
  const refill = useSlip(s => s.refill)
  const setReceipt = useSlip(s => s.setReceipt)
  const setOpen = useSlip(s => s.setOpen)
  return (
    <section className="sb-receipt" aria-label="Bet receipt">
      <h3>BET PLACED ✓</h3>
      {receipt.bets.map(b => (
        <div className="sb-ticket" key={`${b.kind}-${b.id}`}>
          <div className="sb-ticket-id">
            <span>{b.kind === 'parlay' ? `#PL-${b.id}` : `#P-${b.id}`}</span>
            {b.kind === 'parlay' && <span> · {b.labels.length}-leg parlay</span>}
          </div>
          <ul>{b.labels.map((label, i) => <li key={i}>{label}</li>)}</ul>
          <dl>
            <dt>Stake</dt><dd>{formatMoney(b.stake)}</dd>
            <dt>Odds</dt>
            <dd>{formatOdds(b.odds)}{b.shownOdds !== null && ` (was ${formatOdds(b.shownOdds)})`}</dd>
            <dt>To win</dt><dd>{formatMoney(b.toWin)}</dd>
          </dl>
        </div>
      ))}
      <p className="sb-ticket-time">
        Placed {new Date(receipt.placedAt).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}
      </p>
      {remaining > 0 && (
        <p className="sb-slip-error">
          {remaining} {remaining === 1 ? 'bet' : 'bets'} couldn't be placed — still on your slip.
        </p>
      )}
      <div className="sb-receipt-actions">
        <button type="button" onClick={() => { refill(receipt.legs); setReceipt(null) }}>Keep picks</button>
        <button type="button" className="sb-place"
          onClick={() => { setReceipt(null); if (remaining === 0) setOpen(false) }}>Done</button>
      </div>
    </section>
  )
}
