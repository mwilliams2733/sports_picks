import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useRefreshData } from './useRefreshData'
import { api, ApiError } from '../api/client'
import { ToastContext } from './useToast'

function wrapper(client: QueryClient, toast: (message: string, type?: 'success' | 'error' | 'info') => void) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={{ toast }}>{children}</ToastContext.Provider>
    </QueryClientProvider>
  )
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('useRefreshData', () => {
  it('calls the pipeline endpoint with the selected sport', async () => {
    const run = vi.spyOn(api.pipeline, 'run').mockResolvedValue({
      status: 'ok', active_sports: ['mlb'],
      games_stored: 1, odds_stored: 1, props_stored: 0,
      props_analyzed: 0, picks_generated: 2,
      credits_used: 3, credits_remaining_today: 597, credits_remaining_month: 19997,
    })
    const toast = vi.fn()
    const client = newClient()

    const { result } = renderHook(() => useRefreshData(), { wrapper: wrapper(client, toast) })

    act(() => {
      result.current.mutate('mlb')
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(run).toHaveBeenCalledWith('mlb')
  })

  it('sends no sport when the filter is all', async () => {
    const run = vi.spyOn(api.pipeline, 'run').mockResolvedValue({
      status: 'ok', active_sports: [],
      games_stored: 0, odds_stored: 0, props_stored: 0,
      props_analyzed: 0, picks_generated: 0,
      credits_used: 0, credits_remaining_today: 600, credits_remaining_month: 20000,
    })
    const toast = vi.fn()
    const client = newClient()

    const { result } = renderHook(() => useRefreshData(), { wrapper: wrapper(client, toast) })

    act(() => {
      result.current.mutate('all')
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(run).toHaveBeenCalledWith(undefined)
  })

  // This is the test that stops it becoming another dead wire: it proves the
  // hook actually reaches into the query cache on success rather than just
  // calling the endpoint and discarding the result.
  it('invalidates the picks, props and record queries on success', async () => {
    vi.spyOn(api.pipeline, 'run').mockResolvedValue({
      status: 'ok', active_sports: [],
      games_stored: 0, odds_stored: 0, props_stored: 0,
      props_analyzed: 0, picks_generated: 0,
      credits_used: 0, credits_remaining_today: 600, credits_remaining_month: 20000,
    })
    const toast = vi.fn()
    const client = newClient()
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const { result } = renderHook(() => useRefreshData(), { wrapper: wrapper(client, toast) })

    act(() => {
      result.current.mutate('mlb')
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['games'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['props'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['record'] })
  })

  it('a budget-exhausted response reads as budget exhausted', async () => {
    vi.spyOn(api.pipeline, 'run').mockRejectedValue(new ApiError(429, { detail: 'Budget exhausted' }))
    const toast = vi.fn()
    const client = newClient()

    const { result } = renderHook(() => useRefreshData(), { wrapper: wrapper(client, toast) })

    act(() => {
      result.current.mutate('mlb')
    })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/budget/i), 'error')
  })

  it('does not invalidate anything when the call fails', async () => {
    vi.spyOn(api.pipeline, 'run').mockRejectedValue(new Error('network down'))
    const toast = vi.fn()
    const client = newClient()
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const { result } = renderHook(() => useRefreshData(), { wrapper: wrapper(client, toast) })

    act(() => {
      result.current.mutate('mlb')
    })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(invalidateSpy).not.toHaveBeenCalled()
  })
})
