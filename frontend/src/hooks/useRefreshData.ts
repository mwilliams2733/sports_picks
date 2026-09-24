import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { useToast } from './useToast'

/**
 * Wires the refresh button to POST /pipeline/run.
 *
 * Each call spends real Odds API credits against a monthly budget
 * (odds_budget in config.yaml: 20,000/month, 600/day target). The caller
 * MUST keep the button disabled while this mutation is pending
 * (`isPending`) — an un-disabled button that someone can hold down is a
 * bill, not a UX nicety.
 */
export function useRefreshData() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  return useMutation({
    mutationFn: (sport?: string) => api.pipeline.run(sport === 'all' ? undefined : sport),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['games'] })
      queryClient.invalidateQueries({ queryKey: ['props'] })
      queryClient.invalidateQueries({ queryKey: ['record'] })
      toast(`Refreshed: ${data.picks_generated} picks generated, ${data.credits_used} credits used`, 'success')
    },
    onError: (error: unknown) => {
      // A 429 from the backend means BudgetExhaustedError — the daily/monthly
      // odds_budget is spent. That must read distinctly from a generic
      // failure: an operator who sees "something went wrong" will press the
      // button again, which is the worst possible response to a spent budget.
      const isBudgetExhausted = error instanceof ApiError
        ? error.status === 429
        : error instanceof Error && /budget/i.test(error.message)

      if (isBudgetExhausted) {
        toast('Refresh unavailable: API budget exhausted for today/this month', 'error')
      } else {
        const message = error instanceof Error ? error.message : 'Unknown error'
        toast(`Refresh failed: ${message}`, 'error')
      }
    },
  })
}
