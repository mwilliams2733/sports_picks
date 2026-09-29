import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { EmailedGroups, EmailedTrend } from '../types'

export type EmailedKind = 'game' | 'prop'
export type EmailedBy = 'week' | 'month' | 'stars'

export function useEmailedRecord(kind: EmailedKind, by: EmailedBy) {
  const groups = useQuery<EmailedGroups>({
    queryKey: ['emailed', kind, by],
    queryFn: () => api.stats.emailed(kind, by),
  })
  const trend = useQuery<EmailedTrend>({
    queryKey: ['emailed-trend', kind],
    queryFn: () => api.stats.emailedTrend(kind),
  })
  return { groups, trend }
}
