import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWebSocket } from './useWebSocket'
import { useFeedStore } from '../stores/feedStore'

class MockWebSocket {
  static OPEN = 1
  static instances: MockWebSocket[] = []
  url: string
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }

  triggerOpen() {
    this.readyState = 1
    this.onopen?.()
  }

  triggerMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
}

describe('useWebSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    useFeedStore.setState({ events: [], wsConnected: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('connects and marks the feed store connected on open', () => {
    renderHook(() => useWebSocket())
    const ws = MockWebSocket.instances[0]

    act(() => ws.triggerOpen())

    expect(useFeedStore.getState().wsConnected).toBe(true)
  })

  it('reconnects with backoff after the socket closes', () => {
    renderHook(() => useWebSocket())
    const first = MockWebSocket.instances[0]
    act(() => first.triggerOpen())

    act(() => first.close())
    expect(useFeedStore.getState().wsConnected).toBe(false)
    expect(MockWebSocket.instances).toHaveLength(1)

    // Base reconnect delay is 1000ms.
    act(() => { vi.advanceTimersByTime(1000) })
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('sends a heartbeat ping and force-reconnects if no response arrives', () => {
    renderHook(() => useWebSocket())
    const ws = MockWebSocket.instances[0]
    act(() => ws.triggerOpen())

    // Heartbeat interval is 25s.
    act(() => { vi.advanceTimersByTime(25000) })
    expect(ws.sent).toEqual([JSON.stringify({ type: 'ping' })])

    // No pong/message within the 10s heartbeat timeout -> force close.
    act(() => { vi.advanceTimersByTime(10000) })
    expect(ws.readyState).toBe(3)
    expect(useFeedStore.getState().wsConnected).toBe(false)
  })

  it('treats any incoming message as proof of life and skips the timeout', () => {
    renderHook(() => useWebSocket())
    const ws = MockWebSocket.instances[0]
    act(() => ws.triggerOpen())

    act(() => { vi.advanceTimersByTime(25000) })
    // Pong arrives before the heartbeat timeout fires.
    act(() => ws.triggerMessage({ type: 'pong' }))
    act(() => { vi.advanceTimersByTime(10000) })

    // Still open — the pong prevented the heartbeat timeout from firing.
    expect(ws.readyState).toBe(1)
  })
})
