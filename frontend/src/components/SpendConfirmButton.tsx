import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Written after every successful pipeline run (useRefreshData, Backtesting),
// so the confirmation can say what a refresh actually costs.
export const LAST_REFRESH_KEY = 'sp.lastRefreshCredits'

// eslint-disable-next-line react-refresh/only-export-components -- brief's required file shape: this component file also exports the shared storage helper
export function rememberRefreshCost(credits: number): void {
  try { localStorage.setItem(LAST_REFRESH_KEY, String(credits)) } catch { /* storage unavailable */ }
}

function lastRefreshCost(): number | null {
  try {
    const v = localStorage.getItem(LAST_REFRESH_KEY)
    return v == null ? null : Number(v)
  } catch { return null }
}

interface Props {
  label: string
  pendingLabel: string
  pending: boolean
  onConfirm: () => void
}

// A pipeline run spends Odds API credits against the monthly budget. One tap
// must never spend: the first tap shows the cost, the second confirms
// (review brief G.5).
export default function SpendConfirmButton({ label, pendingLabel, pending, onConfirm }: Props) {
  const [asking, setAsking] = useState(false)
  const credits = useQuery({ queryKey: ['credits'], queryFn: () => api.credits.get(), enabled: asking })

  if (pending) return <button className="btn btn-secondary" disabled>{pendingLabel}</button>
  if (!asking) return <button className="btn btn-secondary" onClick={() => setAsking(true)}>{label}</button>

  const last = lastRefreshCost()
  const c = credits.data
  return (
    <span className="spend-confirm" role="group" aria-label="Confirm spending API credits">
      <span className="spend-confirm-text">
        Spends Odds API credits — {last == null ? 'cost not known yet' : `last refresh used ${last} credits`}
        {c ? ` · ${c.daily_used} of ${c.daily_target} used today, ${c.monthly_remaining.toLocaleString()} left this month` : ''}
      </span>
      <button className="btn btn-primary" onClick={() => { setAsking(false); onConfirm() }}>Spend credits and refresh</button>
      <button className="btn btn-secondary" onClick={() => setAsking(false)}>Cancel</button>
    </span>
  )
}
