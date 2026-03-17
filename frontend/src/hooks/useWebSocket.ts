import { useEffect, useRef, useCallback } from 'react'
import { useFeedStore } from '../stores/feedStore'
import type { FeedEvent } from '../stores/feedStore'

const MAX_RECONNECT_DELAY = 30000
const BASE_RECONNECT_DELAY = 1000

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(BASE_RECONNECT_DELAY)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const addEvent = useFeedStore((s) => s.addEvent)
  const setWsConnected = useFeedStore((s) => s.setWsConnected)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`)

    ws.onopen = () => {
      setWsConnected(true)
      reconnectDelay.current = BASE_RECONNECT_DELAY
    }

    ws.onmessage = (event) => {
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
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      setWsConnected(false)
      // Exponential backoff reconnect
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(
          reconnectDelay.current * 2,
          MAX_RECONNECT_DELAY
        )
        connect()
      }, reconnectDelay.current)
    }

    ws.onerror = () => {
      ws.close()
    }

    wsRef.current = ws
  }, [addEvent, setWsConnected])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connected: useFeedStore((s) => s.wsConnected) }
}
