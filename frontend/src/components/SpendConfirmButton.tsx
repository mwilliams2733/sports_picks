import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { lastRefreshCost } from '../lib/refreshCost'

interface Props {
  label: string
  pendingLabel: string
  pending: boolean
  onConfirm: () => void
  className?: string
  showSpinner?: boolean
}

// A pipeline run spends Odds API credits against the monthly budget. One tap
// must never spend: the first tap shows the cost, the second confirms
// (review brief G.5).
export default function SpendConfirmButton({
  label, pendingLabel, pending, onConfirm, className = 'btn btn-secondary', showSpinner = false,
}: Props) {
  const [asking, setAsking] = useState(false)
  const credits = useQuery({ queryKey: ['credits'], queryFn: () => api.credits.get(), enabled: asking })

  if (pending) {
    return (
      <button className={className} disabled>
        {showSpinner && <div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />}
        {pendingLabel}
      </button>
    )
  }
  if (!asking) return <button className={className} onClick={() => setAsking(true)}>{label}</button>

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
