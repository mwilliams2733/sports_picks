import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { GameOddsData, PaperPickData, PropData, UserStats } from '../types'

export type FeedEvent = { message: string; timestamp: string }

/**
 * Server state for the paper-trading page.
 *
 * Keys match useTodaysPicks and useProps exactly (['games','today',sport] and
 * ['props','today',sport,market], both with undefined filters here) so the two
 * pages share one cache entry rather than each fetching the same list.
 */
export function usePaperTradingData() {
  const games = useQuery<GameOddsData[]>({
    queryKey: ['games', 'today', undefined],
    queryFn: () => api.games.today(),
  })

  const props = useQuery<PropData[]>({
    queryKey: ['props', 'today', undefined, undefined],
    queryFn: () => api.props.today(),
  })

  const feed = useQuery<FeedEvent[]>({
    queryKey: ['users', 'feed', 20],
    queryFn: async () => {
      const events = await api.users.feed(20)
      return events.map((e) => ({
        message: e.payload?.message || `${e.event_type}`,
        timestamp: e.created_at,
      }))
    },
  })

  return { games, props, feed }
}

/**
 * One user's picks and stats.
 *
 * Both sit under ['users', id, ...], so the invalidateQueries({queryKey:
 * ['users']}) the page already fires after placing a pick refreshes the
 * leaderboard and this user's rows together -- which is what the old code was
 * doing by hand when it re-ran selectUser(selectedUser).
 */
export function useUserDetail(userId: number | undefined) {
  const picks = useQuery<PaperPickData[]>({
    queryKey: ['users', userId, 'picks'],
    queryFn: () => api.users.picks(userId as number),
    enabled: userId !== undefined,
  })

  const stats = useQuery<UserStats>({
    queryKey: ['users', userId, 'stats'],
    queryFn: () => api.users.stats(userId as number),
    enabled: userId !== undefined,
  })

  return { picks, stats }
}
