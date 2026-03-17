import { useQuery } from '@tanstack/react-query'
import api from '../api/client'
import { PickData, RecordData, GameOddsData } from '../types'

export function useTodaysPicks(sport?: string) {
  const sportParam = sport === 'all' ? undefined : sport

  const picks = useQuery<PickData[]>({
    queryKey: ['picks', 'today', sportParam],
    queryFn: () => api.picks.today(sportParam),
  })

  const record = useQuery<RecordData>({
    queryKey: ['record', sportParam],
    queryFn: () => api.stats.record(sportParam),
  })

  const games = useQuery<GameOddsData[]>({
    queryKey: ['games', 'today', sportParam],
    queryFn: () => api.games.today(sportParam),
  })

  return { picks, record, games }
}
