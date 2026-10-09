import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { loadSeen, newWins, saveSeen, ticketKey } from '../lib/bets'
import { formatMoney } from '../lib/board'

const COLORS = ['#2fe37a', '#ffb020', '#f3f5f8', '#8b5cf6', '#ff4d5e']
// Fixed pseudo-random spread so the confetti is deterministic (and testable).
const CONFETTI: CSSProperties[] = Array.from({ length: 28 }, (_, i) => ({
  left: `${(i * 37) % 100}%`,
  animationDelay: `${((i * 13) % 10) / 20}s`,
  background: COLORS[i % COLORS.length],
}))

/** "WINNER 🎉 +$X" the first time a settled win appears on this device. */
export default function Celebrations() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)
  const meId = me?.id
  // Read-only here (the seen list is written by the effect below), so this is
  // safe to compute during render. React Query keeps the same `data` object
  // while nothing changed, so a refetch doesn't re-run it.
  const pending = useMemo(
    () => (meId === undefined || !bets.data ? null : newWins(bets.data.tickets, loadSeen(meId))),
    [meId, bets.data])
  const [dismissed, setDismissed] = useState<string | null>(null)

  useEffect(() => {
    if (meId !== undefined && pending) saveSeen(meId, pending.seen)
  }, [meId, pending])

  const wins = pending?.wins ?? []
  const key = wins.map(ticketKey).join(',')
  const showing = wins.length > 0 && dismissed !== key

  useEffect(() => {
    if (!showing) return
    const timer = setTimeout(() => setDismissed(key), 6000)
    return () => clearTimeout(timer)
  }, [showing, key])

  if (!showing) return null
  const amount = wins.reduce((a, t) => a + (t.payout ?? 0), 0)
  const parlay = wins.some(t => t.kind === 'parlay')
  return (
    <div className={`sb-win${parlay ? ' sb-win-big' : ''}`} role="status" onClick={() => setDismissed(key)}>
      <div className="sb-confetti" aria-hidden="true">
        {CONFETTI.map((style, i) => <span key={i} style={style} />)}
      </div>
      <strong>WINNER 🎉 +{formatMoney(amount)}</strong>
      {parlay && <small>Parlay hit!</small>}
    </div>
  )
}
