import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { useSlip } from '../stores/slipStore'
import { getPin, setPin } from '../lib/secrets'
import { formatOdds, marketKey, parlayEstimate } from '../lib/quotes'
import { formatMoney } from '../lib/board'
import { QUICK_STAKES, betRequest, hasSameGame, isClosed, refusal, toWin } from '../lib/slip'
import type { ReceiptBet, SlipLeg } from '../types'
import BetReceipt from './BetReceipt'

function StakeInput({ value, max, label, onChange }: {
  value: number; max: number; label: string; onChange: (n: number) => void
}) {
  return (
    <div className="sb-stake">
      <input type="number" inputMode="decimal" min={0} step="any" aria-label={label} placeholder="Stake"
        value={value || ''} onChange={e => onChange(Math.max(0, Number(e.target.value) || 0))} />
      {QUICK_STAKES.map(n => (
        <button key={n} type="button" className="sb-stake-chip" onClick={() => onChange(n)}>${n}</button>
      ))}
      <button type="button" className="sb-stake-chip" onClick={() => onChange(Math.floor(max * 100) / 100)}>Max</button>
    </div>
  )
}

function LegRow({ l, singles, available, now }: { l: SlipLeg; singles: boolean; available: number; now: Date }) {
  const remove = useSlip(s => s.remove)
  const setStake = useSlip(s => s.setStake)
  const closed = isClosed(l, now)
  return (
    <li className={`sb-slip-leg${l.error ? ' has-error' : ''}`}>
      <div className="sb-slip-leg-head">
        <div>
          <strong>{l.label}</strong>
          <small>{l.gameLabel}</small>
        </div>
        <span className="sb-slip-odds">{formatOdds(l.odds)}</span>
        <button type="button" className="sb-slip-remove" aria-label={`Remove ${l.label}`}
          onClick={() => remove(l.leg)}>✕</button>
      </div>
      {l.movedFrom && (
        <p className="sb-slip-moved">
          Odds changed {formatOdds(l.movedFrom.odds)} → {formatOdds(l.odds)}
          {l.movedFrom.line !== l.line && ` (line ${l.movedFrom.line} → ${l.line})`}
        </p>
      )}
      {closed && <p className="sb-slip-error">Betting closed</p>}
      {l.error && <p className="sb-slip-error" role="alert">{l.error}</p>}
      {singles && !closed && (
        <>
          <StakeInput value={l.stake} max={available} label={`Stake for ${l.label}`} onChange={n => setStake(l.leg, n)} />
          <small className="sb-slip-towin">To win {formatMoney(toWin(l.stake, l.odds))}</small>
        </>
      )}
    </li>
  )
}

type Placed = { bets: ReceiptBet[]; placedLegs: SlipLeg[] }

export default function BetSlip() {
  const s = useSlip()
  const queryClient = useQueryClient()
  const { currentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const me = users.data?.find(u => u.name === currentUserName)
  const [pinInput, setPinInput] = useState('')
  const [placing, setPlacing] = useState(false)
  const [slipError, setSlipError] = useState<string | null>(null)

  if (s.legs.length === 0 && s.receipt === null) return null

  const now = new Date()
  const storedPin = me ? getPin(me.id) : null
  const pin = storedPin ?? pinInput
  const available = me?.available_balance ?? 0
  const parlay = s.mode === 'parlay'
  const openLegs = s.legs.filter(l => !isClosed(l, now))
  const estimate = parlayEstimate(s.legs.map(l => l.odds))
  const total = parlay ? s.parlayStake : openLegs.reduce((a, l) => a + l.stake, 0)
  const totalToWin = parlay
    ? (estimate ? s.parlayStake * (estimate.decimal - 1) : 0)
    : openLegs.reduce((a, l) => a + toWin(l.stake, l.odds), 0)
  const moved = s.legs.some(l => l.movedFrom)
  const blocked = !me ? 'Choose a player in the top bar, or join the league, to bet.'
    : parlay && s.legs.length < 2 ? 'A parlay needs at least 2 legs.'
    : parlay && openLegs.length !== s.legs.length ? 'Remove the closed legs to place this parlay.'
    : total > available ? `Over your available balance (${formatMoney(available)}).`
    : null
  const canPlace = !!me && !placing && !blocked && total > 0 && /^\d{4,6}$/.test(pin)

  const forgetPin = () => {
    if (me) setPin(me.id, null)
    setPinInput('')
  }

  // Singles go one at a time, each with its own outcome (spec §6). A 401
  // stops the loop: the same wrong PIN would fail every leg and count
  // toward the lockout.
  const placeSingles = async (uid: number): Promise<Placed> => {
    const out: Placed = { bets: [], placedLegs: [] }
    for (const l of openLegs.filter(x => x.stake > 0)) {
      try {
        const res = await api.users.placePick(uid, { ...betRequest(l, s.acceptAnyOdds), stake: l.stake }, pin)
        out.bets.push({ id: res.id, kind: 'single', labels: [l.label], stake: l.stake, odds: res.odds,
          shownOdds: res.odds === l.odds ? null : l.odds, toWin: toWin(l.stake, res.odds) })
        out.placedLegs.push(l)
        s.remove(l.leg)
      } catch (e) {
        const r = refusal(e)
        if (r.kind === 'moved') {
          s.priceMoved(l.leg, r.odds, r.line, r.pickValue)
        } else {
          s.setError(l.leg, r.message)
          if (r.kind === 'pin') { forgetPin(); break }
        }
      }
    }
    return out
  }

  const placeParlay = async (uid: number): Promise<Placed> => {
    const legs = s.legs
    const stake = s.parlayStake
    try {
      const res = await api.users.placeParlay(uid,
        { legs: legs.map(l => betRequest(l, s.acceptAnyOdds)), stake }, pin)
      const shown = parlayEstimate(legs.map(l => l.odds))
      s.clear()
      return { bets: [{ id: res.id, kind: 'parlay', labels: legs.map(l => l.label), stake, odds: res.combined_odds,
        shownOdds: shown && shown.american !== res.combined_odds ? shown.american : null,
        toWin: res.potential_payout }], placedLegs: legs }
    } catch (e) {
      const r = refusal(e)
      if (r.kind === 'moved') {
        const hit = r.leg !== undefined ? legs[r.leg] : undefined
        if (hit) s.priceMoved(hit.leg, r.odds, r.line, r.pickValue)
        else setSlipError('A price moved — check the legs and try again.')
      } else {
        setSlipError(r.message)
        if (r.kind === 'pin') forgetPin()
      }
      return { bets: [], placedLegs: [] }
    }
  }

  const handlePlace = async () => {
    if (!me || !canPlace) return
    setPlacing(true)
    setSlipError(null)
    try {
      const { bets, placedLegs } = parlay ? await placeParlay(me.id) : await placeSingles(me.id)
      if (bets.length > 0) {
        setPin(me.id, pin)
        setPinInput('')
        s.setReceipt({ bets, legs: placedLegs, placedAt: new Date().toISOString() })
      }
      queryClient.invalidateQueries({ queryKey: ['users'] })
      // Board and quote prices: a moved price must show on its tile too.
      queryClient.invalidateQueries({ queryKey: ['paper'] })
    } finally {
      setPlacing(false)
    }
  }

  const placeLabel = placing ? 'Placing…'
    : moved ? 'Accept & Place'
    : parlay ? 'Place Parlay'
    : s.legs.length > 1 ? 'Place Bets' : 'Place Bet'

  return (
    <>
      {s.legs.length > 0 && !s.open && (
        <button type="button" className="sb-slip-bar" onClick={() => s.setOpen(true)}>
          <span className="sb-slip-count">{s.legs.length}</span> Bet Slip
          <span className="sb-slip-bar-total">{formatMoney(total)}</span>
        </button>
      )}
      <aside className={`sb-slip${s.open ? ' open' : ''}`} aria-label="Bet slip">
        <header className="sb-slip-head">
          <h2>Bet Slip <span className="sb-slip-count">{s.legs.length}</span></h2>
          <button type="button" className="sb-slip-close" aria-label="Close bet slip"
            onClick={() => s.setOpen(false)}>✕</button>
        </header>
        {s.receipt ? <BetReceipt receipt={s.receipt} remaining={s.legs.filter(l => l.error || l.movedFrom).length} /> : (
          <>
            <div className="sb-slip-tabs" role="tablist" aria-label="Bet type">
              <button type="button" role="tab" aria-selected={!parlay} onClick={() => s.setMode('singles')}>Singles</button>
              <button type="button" role="tab" aria-selected={parlay} disabled={s.legs.length < 2}
                onClick={() => s.setMode('parlay')}>Parlay</button>
            </div>
            {!parlay && s.legs.length > 1 && (
              <label className="sb-slip-check">
                <input type="checkbox" checked={s.sameStake} onChange={e => s.setSameStake(e.target.checked)} />
                Same stake for all
              </label>
            )}
            <ul className="sb-slip-legs">
              {s.legs.map(l => (
                <LegRow key={marketKey(l.leg)} l={l} singles={!parlay} available={available} now={now} />
              ))}
            </ul>
            {parlay && (
              <div className="sb-slip-parlay">
                <div className="sb-slip-parlay-odds">
                  <span>{s.legs.length}-leg parlay</span>
                  {hasSameGame(s.legs) && <span className="sb-sgp">SGP</span>}
                  <strong>{estimate ? formatOdds(estimate.american) : '—'}</strong>
                </div>
                <small>Estimate — the server prices every leg again when you place it.</small>
                <StakeInput value={s.parlayStake} max={available} label="Parlay stake" onChange={s.setParlayStake} />
              </div>
            )}
            <label className="sb-slip-check">
              <input type="checkbox" checked={s.acceptAnyOdds} onChange={e => s.setAcceptAnyOdds(e.target.checked)} />
              Accept any odds changes
            </label>
            {me && !storedPin && (
              <input className="input" aria-label="PIN" type="password" inputMode="numeric" autoComplete="off"
                maxLength={6} placeholder="PIN (4–6 digits)" value={pinInput}
                onChange={e => setPinInput(e.target.value)} />
            )}
            {(slipError ?? blocked) && <p className="sb-slip-error" role="alert">{slipError ?? blocked}</p>}
            <footer className="sb-slip-foot">
              <div><small>Total stake</small><strong>{formatMoney(total)}</strong></div>
              <div><small>To win</small><strong>{formatMoney(totalToWin)}</strong></div>
            </footer>
            <button type="button" className="sb-place" disabled={!canPlace} onClick={handlePlace}>{placeLabel}</button>
            <button type="button" className="sb-slip-clear" onClick={s.clear}>Clear slip</button>
          </>
        )}
      </aside>
    </>
  )
}
