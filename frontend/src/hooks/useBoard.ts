import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Same cadence as useQuotes: a price on screen is never far from the one the
// server will charge. `retry: false` so an unreachable server shows as
// offline at once instead of after silent retries.
export function useBoard() {
  return useQuery({
    queryKey: ['paper', 'board'],
    queryFn: api.paper.board,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  })
}
