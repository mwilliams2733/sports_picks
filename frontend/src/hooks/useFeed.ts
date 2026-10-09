import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/** The league feed. The WebSocket invalidates ['users','feed'] on every feed
 *  message, so this refreshes live; the 60 s refetch covers events the
 *  scheduler writes (it has no socket) and a dropped connection. */
export function useFeed(limit = 50) {
  return useQuery({
    queryKey: ['users', 'feed', limit],
    queryFn: () => api.users.feed(limit),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}
