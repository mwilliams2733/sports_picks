import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { LeaderboardRow } from '../types'

export function useRankings() {
  return useQuery<LeaderboardRow[]>({
    queryKey: ['users', 'rankings'],
    queryFn: () => api.users.leaderboard(),
    refetchInterval: 30000,
  })
}
