import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Quotes refetch every minute and on window focus so a price on screen is
// never far from the price the server will charge.
const LIVE = { refetchInterval: 60_000, refetchOnWindowFocus: true } as const

export function useGameQuotes(gameId: number | null) {
  return useQuery({
    queryKey: ['paper', 'quotes', gameId],
    queryFn: () => api.paper.quotes(gameId as number),
    enabled: gameId != null,
    ...LIVE,
  })
}

export function usePropQuotes(gameId: number | null) {
  return useQuery({
    queryKey: ['paper', 'prop-quotes', gameId],
    queryFn: () => api.paper.propQuotes(gameId as number),
    enabled: gameId != null,
    ...LIVE,
  })
}
