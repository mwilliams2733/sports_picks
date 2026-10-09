import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { formatMoney } from '../lib/board'
import { cashOutRefusal } from '../lib/cashout'
import { getPin, setPin } from '../lib/secrets'
import type { Ticket } from '../types'

/** Cash out an open ticket (sportsbook spec §9): the offer, a confirm step,
 *  and the server's answer. Never pays less than the figure confirmed: a
 *  lower offer comes back as offer_changed and is asked again. */
export default function CashOutButton({ t, userId }: { t: Ticket; userId: number }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [working, setWorking] = useState(false)
  const [changedTo, setChangedTo] = useState<number | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [pinInput, setPinInput] = useState('')
  const view = t.cash_out
  if (!view) return null
  if (!view.available || view.offer === undefined) {
    return (
      <div className="sb-cashout">
        <button type="button" className="sb-cashout-btn" disabled>Cash out</button>
        <small className="sb-cashout-why">{view.message}</small>
      </div>
    )
  }
  const offer = changedTo ?? view.offer
  const stored = getPin(userId)
  const pin = stored ?? pinInput
  const pinOk = /^\d{4,6}$/.test(pin)

  const confirm = async () => {
    setWorking(true)
    setMessage(null)
    try {
      await api.users.cashOut(userId, { bet_id: t.id, kind: t.kind, expected_offer: offer }, pin)
      if (!stored) setPin(userId, pin)
      setConfirming(false)
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    } catch (e) {
      const r = cashOutRefusal(e)
      setMessage(r.message)
      if (r.kind === 'changed') setChangedTo(r.offer)
      if (r.kind === 'pin') { setPin(userId, null); setPinInput('') }
    } finally {
      setWorking(false)
    }
  }

  if (!confirming) {
    return (
      <div className="sb-cashout">
        <button type="button" className="sb-cashout-btn" onClick={() => setConfirming(true)}>
          Cash out {formatMoney(offer)}
        </button>
      </div>
    )
  }
  return (
    <div className="sb-cashout sb-cashout-confirm" role="group" aria-label="Confirm cash out">
      <p>Cash out for <strong>{formatMoney(offer)}</strong>? Stake {formatMoney(t.stake)}.</p>
      {message && <p className="sb-slip-error" role="alert">{message}</p>}
      {!getPin(userId) && (
        <label className="sb-cashout-pin">PIN
          <input type="password" inputMode="numeric" autoComplete="off" value={pinInput}
            onChange={e => setPinInput(e.target.value)} />
        </label>
      )}
      <div className="sb-cashout-actions">
        <button type="button" className="sb-cashout-btn" disabled={working || !pinOk} onClick={confirm}
          aria-label="Confirm cash out">{working ? 'Cashing out…' : `Confirm ${formatMoney(offer)}`}</button>
        <button type="button" className="sb-cashout-keep" disabled={working}
          onClick={() => { setConfirming(false); setMessage(null) }}>Keep bet</button>
      </div>
    </div>
  )
}
