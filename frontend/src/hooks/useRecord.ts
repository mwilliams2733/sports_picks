import { useQuery } from '@tanstack/react-query'
import api from '../api/client'
import { RecordData, DailyData, PickData } from '../types'

export function useRecord(sport?: string) {
  const sportParam = sport === 'all' ? undefined : sport

  const record = useQuery<RecordData>({
    queryKey: ['record', sportParam],
    queryFn: () => api.stats.record(sportParam),
  })

  const daily = useQuery<DailyData[]>({
    queryKey: ['daily', sportParam],
    queryFn: () => api.stats.daily(),
  })

  const history = useQuery<PickData[]>({
    queryKey: ['picks', 'history'],
    queryFn: () => api.picks.history(),
  })

  return { record, daily, history }
}
