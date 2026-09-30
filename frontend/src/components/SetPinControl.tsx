import { useState } from 'react'
import { api, ApiError, getErrorMessage } from '../api/client'
import { useToast } from '../hooks/useToast'

interface Props {
  userId: number
  userName: string
  hasOwnerKey: boolean
  onSaved: () => void
}

/** Owner-only: set or reset a player's PIN inline. No browser dialog, and the
 *  PIN is never logged or shown in a toast. */
export default function SetPinControl({ userId, userName, hasOwnerKey, onSaved }: Props) {
  const [open, setOpen] = useState(false)
  const [pin, setPinValue] = useState('')
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const close = () => { setOpen(false); setPinValue('') }

  const save = async () => {
    setSaving(true)
    try {
      await api.users.setPin(userId, pin)
      toast(`PIN set for ${userName}`, 'success')
      close()
      onSaved()
    } catch (e) {
      toast(e instanceof ApiError && e.status === 403
        ? 'Owner key required — add it above' : getErrorMessage(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return <button className="btn-bet set-pin" style={{ fontSize: '0.75rem' }}
      onClick={() => setOpen(true)}>Set PIN</button>
  }
  if (!hasOwnerKey) {
    return (
      <span className="set-pin">
        <span className="text-muted" style={{ fontSize: '0.75rem' }}>Save the owner key above first.</span>{' '}
        <button className="btn-bet" style={{ fontSize: '0.7rem' }} onClick={close}>Cancel</button>
      </span>
    )
  }
  return (
    <span className="set-pin">
      <input
        aria-label={`New PIN for ${userName}`}
        className="input"
        type="password"
        inputMode="numeric"
        autoComplete="off"
        maxLength={6}
        placeholder="4–6 digits"
        value={pin}
        onChange={e => setPinValue(e.target.value)}
      />
      <button className="btn-bet" style={{ fontSize: '0.7rem' }}
        disabled={saving || !/^\d{4,6}$/.test(pin)} onClick={save}>Save</button>
      <button className="btn-bet" style={{ fontSize: '0.7rem' }} onClick={close}>Cancel</button>
    </span>
  )
}
