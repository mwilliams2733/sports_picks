import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, ApiError } from './client'
import { setOwnerKey } from '../lib/secrets'

function lastHeaders(): Record<string, string> {
  const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)!
  return (call[1]?.headers ?? {}) as Record<string, string>
}

beforeEach(() => {
  // A fresh Response per call: Response.json() can only be read once per
  // instance, and mockResolvedValue would hand back the same instance to
  // every call (this test calls the mocked fetch more than once).
  globalThis.fetch = vi.fn(() => Promise.resolve(new Response('{}', { status: 200 }))) as unknown as typeof fetch
  setOwnerKey(null)
})

describe('auth headers', () => {
  it('sends the owner key on writes once it is stored', async () => {
    await api.users.grade()
    expect(lastHeaders()['X-Owner-Key']).toBeUndefined()
    setOwnerKey('k')
    await api.users.grade()
    expect(lastHeaders()['X-Owner-Key']).toBe('k')
  })

  it('sends the player PIN on a bet, as typed', async () => {
    await api.users.placePick(3, { game_id: 1, pick_type: 'moneyline', pick_value: 'HOME ML', odds: -110, stake: 10 }, '0123')
    expect(lastHeaders()['X-Player-Pin']).toBe('0123')
  })

  it('sends the PIN in the body when joining', async () => {
    await api.users.create('amy', '4321')
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)!
    expect(JSON.parse(call[1].body as string)).toEqual({ name: 'amy', pin: '4321' })
  })

  it('sends no owner key on a read, even when one is stored', async () => {
    setOwnerKey('k')
    await api.users.list()
    expect(lastHeaders()['X-Owner-Key']).toBeUndefined()
  })
})

describe('ApiError', () => {
  it('uses the first validation message when detail is a list', () => {
    const err = new ApiError(422, { detail: [{ msg: 'PIN must be 4-6 digits' }] })
    expect(err.message).toContain('PIN must be 4-6 digits')
  })
})
