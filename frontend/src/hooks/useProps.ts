import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PropData } from '../types'

export function useProps(sport?: string, market?: string) {
  const sportParam = sport === 'all' ? undefined : sport

  const props = useQuery<PropData[]>({
    queryKey: ['props', 'today', sportParam, market],
    queryFn: () => api.props.today(sportParam, market),
  })

  const markets = useQuery<{ key: string; label: string }[]>({
    queryKey: ['props', 'markets'],
    queryFn: () => api.props.markets(),
  })

  return { props, markets }
}
