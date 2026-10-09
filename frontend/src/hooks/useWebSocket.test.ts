import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useWebSocket } from './useWebSocket'

class FakeSocket {
  static last: FakeSocket
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() { FakeSocket.last = this }
  send() {}
  close() {}
}

beforeEach(() => { vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket) })
afterEach(() => vi.unstubAllGlobals())

describe('useWebSocket', () => {
  it('refreshes the feed and the money queries on a feed message (settled bets included)', () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children)
    renderHook(() => useWebSocket(), { wrapper })
    FakeSocket.last.onmessage?.({ data: JSON.stringify({ type: 'pick_pushed', data: { message: 'x' } }) })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['users'] })
  })
})
