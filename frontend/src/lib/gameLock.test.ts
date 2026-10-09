import { describe, it, expect, vi, afterEach } from 'vitest'
import { getGameLockState } from './gameLock'

afterEach(() => vi.useRealTimers())

describe('getGameLockState', () => {
  it('locks a game the live job has marked in progress, whatever its start time says', () => {
    expect(getGameLockState('in_progress', '2099-01-01T00:00:00Z')).toBe('locked')
  })
  it('reads a naive start time as UTC, as /games/today sends it', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T17:30:00Z'))
    expect(getGameLockState('scheduled', '2026-10-11T17:00:00')).toBe('locked')
    expect(getGameLockState('scheduled', '2026-10-11T18:00:00')).toBe('open')
    expect(getGameLockState('final', '2026-10-11T17:00:00')).toBe('final')
  })
})
