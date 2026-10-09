import { ApiError } from '../api/client'

export type CashOutRefusal =
  | { kind: 'changed'; offer: number; message: string }
  | { kind: 'pin'; message: string }
  | { kind: 'error'; message: string }

/** What a refused cash out means for the button (sportsbook spec §9, §11). */
export function cashOutRefusal(e: unknown): CashOutRefusal {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: unknown } | null)?.detail as { reason?: unknown; offer?: unknown } | undefined
    if (e.status === 409 && d && d.reason === 'offer_changed' && typeof d.offer === 'number') {
      return { kind: 'changed', offer: d.offer, message: e.message }
    }
    if (e.status === 401) return { kind: 'pin', message: e.message }
    return { kind: 'error', message: e.message }
  }
  return { kind: 'error', message: "Couldn't reach the server — try again." }
}
