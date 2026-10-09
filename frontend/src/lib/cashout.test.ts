import { describe, it, expect } from 'vitest'
import { cashOutRefusal } from './cashout'
import { ApiError } from '../api/client'

describe('cashOutRefusal', () => {
  it('reads a changed offer', () => {
    const e = new ApiError(409, { detail: { reason: 'offer_changed', offer: 85, message: 'The offer changed to $85.00.' } })
    expect(cashOutRefusal(e)).toEqual({ kind: 'changed', offer: 85, message: 'The offer changed to $85.00.' })
  })
  it('reads a PIN failure', () => {
    expect(cashOutRefusal(new ApiError(401, { detail: 'Wrong PIN' }))).toEqual({ kind: 'pin', message: 'Wrong PIN' })
  })
  it("reads any other refusal by the server's message", () => {
    const e = new ApiError(409, { detail: { reason: 'game_started', message: 'A game in this bet has started — cash out is pre-game only.' } })
    expect(cashOutRefusal(e)).toEqual({ kind: 'error', message: 'A game in this bet has started — cash out is pre-game only.' })
  })
  it('reads a network failure', () => {
    expect(cashOutRefusal(new TypeError('fetch failed'))).toEqual({ kind: 'error', message: "Couldn't reach the server — try again." })
  })
})
