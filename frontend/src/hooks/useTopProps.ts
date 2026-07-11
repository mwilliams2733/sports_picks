import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PropData } from '../types'

export function useTopProps(sport?: string) {
  const sportParam = sport === 'all' ? undefined : sport

  return useQuery<PropData[]>({
    queryKey: ['props', 'today', sportParam],
    queryFn: () => api.props.today(sportParam),
    select: (allProps) =>
      allProps
        .filter((p) => p.confidence !== null && p.confidence >= 3)
        .sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))
        .slice(0, 10),
  })
}
