import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Under ['users', ...] like useMyBets, so a placed or cashed-out bet (which
// invalidates ['users']) refreshes a player's results too.
export function usePlayerStats(userId: number) {
  return useQuery({
    queryKey: ['users', 'stats', userId],
    queryFn: () => api.users.stats(userId),
    refetchInterval: 60_000,
  })
}
