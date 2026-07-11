import { useEffect, useRef, useCallback } from 'react'
import { useFeedStore } from '../stores/feedStore'
import type { FeedEvent } from '../stores/feedStore'

const MAX_RECONNECT_DELAY = 30000
const BASE_RECONNECT_DELAY = 1000
const HEARTBEAT_INTERVAL = 25000
const HEARTBEAT_TIMEOUT = 10000

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(BASE_RECONNECT_DELAY)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const heartbeatTimer = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const heartbeatTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const addEvent = useFeedStore((s) => s.addEvent)
  const setWsConnected = useFeedStore((s) => s.setWsConnected)
  const connectRef = useRef<() => void>(() => {})

  const stopHeartbeat = useCallback(() => {
    if (heartbeatTimer.current) clearInterval(heartbeatTimer.current)
    if (heartbeatTimeout.current) clearTimeout(heartbeatTimeout.current)
  }, [])

  const startHeartbeat = useCallback((ws: WebSocket) => {
    stopHeartbeat()
    heartbeatTimer.current = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) return
      try {
        ws.send(JSON.stringify({ type: 'ping' }))
      } catch (err) {
        console.error('WebSocket ping failed:', err)
      }
      // If we don't hear anything back (pong or otherwise) before the next
      // heartbeat tick, the connection is silently dead — force a reconnect.
      heartbeatTimeout.current = setTimeout(() => {
        console.error('WebSocket heartbeat timed out; forcing reconnect')
        ws.close()
      }, HEARTBEAT_TIMEOUT)
    }, HEARTBEAT_INTERVAL)
  }, [stopHeartbeat])

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`)

    ws.onopen = () => {
      setWsConnected(true)
      reconnectDelay.current = BASE_RECONNECT_DELAY
      startHeartbeat(ws)
    }

    ws.onmessage = (event) => {
      // Any message (including pong) proves the connection is alive.
      if (heartbeatTimeout.current) clearTimeout(heartbeatTimeout.current)

      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'pong') return

        if (['pick_placed', 'pick_won', 'pick_lost', 'streak', 'feed_event'].includes(msg.type)) {
          const feedEvent: FeedEvent = {
            id: crypto.randomUUID(),
            type: msg.data?.event_type || msg.type,
            userName: msg.data?.user_name || '',
            message: msg.data?.message || '',
            timestamp: msg.data?.timestamp || new Date().toISOString(),
          }
          addEvent(feedEvent)
        }
      } catch (err) {
        console.error('WebSocket received malformed message:', event.data, err)
      }
    }

    ws.onclose = () => {
      setWsConnected(false)
      stopHeartbeat()
      // Exponential backoff reconnect
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(
          reconnectDelay.current * 2,
          MAX_RECONNECT_DELAY
        )
        connectRef.current()
      }, reconnectDelay.current)
    }

    ws.onerror = (event) => {
      console.error('WebSocket error:', event)
      ws.close()
    }

    wsRef.current = ws
  }, [addEvent, setWsConnected, startHeartbeat, stopHeartbeat])

  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      stopHeartbeat()
      wsRef.current?.close()
    }
  }, [connect, stopHeartbeat])

  return { connected: useFeedStore((s) => s.wsConnected) }
}
