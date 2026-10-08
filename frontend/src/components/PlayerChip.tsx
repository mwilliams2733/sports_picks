import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, getErrorMessage } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { formatMoney } from '../lib/board'
import { setPin } from '../lib/secrets'

export default function PlayerChip() {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const [open, setOpen] = useState(false)
  const [joining, setJoining] = useState(false)
  const [name, setName] = useState('')
  const [newPin, setNewPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const me = users.data?.find(u => u.name === currentUserName)

  const join = async () => {
    const n = name.trim()
    if (!n) { setError('Enter a name.'); return }
    if (!/^\d{4,6}$/.test(newPin)) { setError('PIN must be 4–6 digits.'); return }
    try {
      const res = await api.users.create(n, newPin)
      setPin(res.id, newPin)
      setCurrentUserName(res.name)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setJoining(false); setOpen(false); setName(''); setNewPin(''); setError(null)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  return (
    <div style={{ marginLeft: 'auto', position: 'relative' }}>
      <button type="button" className="sb-chip" onClick={() => setOpen(o => !o)} aria-haspopup="menu">
        {me ? `${me.name} · ${formatMoney(me.available_balance)}` : 'Choose player'}
      </button>
      {open && (
        <div className="sb-chip-menu" role="menu">
          {(users.data ?? []).map(u => (
            <button key={u.id} type="button" aria-current={u.name === currentUserName}
              onClick={() => { setCurrentUserName(u.name); setOpen(false) }}>
              <span>{u.name}</span><span>{formatMoney(u.available_balance)}</span>
            </button>
          ))}
          {joining ? (
            <div className="sb-join">
              <input className="input" aria-label="Your name" placeholder="Your name" value={name}
                onChange={e => setName(e.target.value)} />
              <input className="input" aria-label="New PIN" type="password" inputMode="numeric" autoComplete="off"
                maxLength={6} placeholder="PIN (4–6 digits)" value={newPin} onChange={e => setNewPin(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && join()} />
              {error && <p className="sb-slip-error" role="alert">{error}</p>}
              <button type="button" className="sb-place" onClick={join}>Join</button>
            </div>
          ) : (
            <button type="button" onClick={() => setJoining(true)}>+ Join the league</button>
          )}
        </div>
      )}
    </div>
  )
}
