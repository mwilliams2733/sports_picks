import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { formatMoney } from '../lib/board'

export default function PlayerChip() {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const [open, setOpen] = useState(false)
  const me = users.data?.find(u => u.name === currentUserName)
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
        </div>
      )}
    </div>
  )
}
