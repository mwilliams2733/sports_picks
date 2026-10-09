import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Under ['users', ...] so a placed bet (which invalidates ['users']) refreshes
// My Bets, the badge and the celebration check at once.
export function useMyBets(userId: number | undefined) {
  return useQuery({
    queryKey: ['users', 'bets', userId],
    queryFn: () => api.users.bets(userId as number),
    enabled: userId !== undefined,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}
