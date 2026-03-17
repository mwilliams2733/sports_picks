import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { UserProfile } from '../types'

export function useLeaderboard() {
  return useQuery<UserProfile[]>({
    queryKey: ['users', 'leaderboard'],
    queryFn: () => api.users.list(),
    refetchInterval: 30000,
  })
}
