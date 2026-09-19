import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useUserDetail, usePaperTradingData } from './usePaperTrading'
import { api } from '../api/client'

/**
 * PaperTrading used to fetch its own server state with setState-after-await,
 * and refreshed a user's picks by calling selectUser(user) again by hand.
 * Those calls were removed because each sat directly under an
 * invalidateQueries({queryKey: ['users']}) that now covers the user's rows
 * too. That only holds while picks and stats live UNDER the 'users' key, so
 * it is asserted rather than assumed.
 */

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

function newClient() {
  return new QueryClient({
    // staleTime mirrors App.tsx. It is the whole point of the shared keys:
    // a list another page already holds is served without a refetch. With
    // the library default of 0 every one of these would refetch on mount and
    // the sharing would buy nothing.
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 30_000 } },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('useUserDetail', () => {
  it('fetches nothing until a user is selected', async () => {
    const picks = vi.spyOn(api.users, 'picks')
    const stats = vi.spyOn(api.users, 'stats')

    const { result } = renderHook(() => useUserDetail(undefined), {
      wrapper: wrapper(newClient()),
    })

    expect(picks).not.toHaveBeenCalled()
    expect(stats).not.toHaveBeenCalled()
    expect(result.current.picks.data).toBeUndefined()
  })

  it('loads the selected user picks and stats', async () => {
    vi.spyOn(api.users, 'picks').mockResolvedValue([
      { id: 1, pick_value: 'HOME ML' },
    ] as never)
    vi.spyOn(api.users, 'stats').mockResolvedValue({ all_time: 3 } as never)

    const { result } = renderHook(() => useUserDetail(7), {
      wrapper: wrapper(newClient()),
    })

    await waitFor(() => expect(result.current.picks.data).toHaveLength(1))
    expect(api.users.picks).toHaveBeenCalledWith(7)
    await waitFor(() => expect(result.current.stats.data).toEqual({ all_time: 3 }))
  })

  it('refetches when the users key is invalidated', async () => {
    // The whole reason the manual selectUser(selectedUser) refresh could go.
    const picks = vi.spyOn(api.users, 'picks').mockResolvedValue([] as never)
    vi.spyOn(api.users, 'stats').mockResolvedValue({} as never)
    const client = newClient()

    const { result } = renderHook(() => useUserDetail(7), {
      wrapper: wrapper(client),
    })
    await waitFor(() => expect(result.current.picks.isSuccess).toBe(true))
    expect(picks).toHaveBeenCalledTimes(1)

    await client.invalidateQueries({ queryKey: ['users'] })

    await waitFor(() => expect(picks).toHaveBeenCalledTimes(2))
  })
})

describe('usePaperTradingData', () => {
  it('shares its game and prop cache entries with the other pages', async () => {
    // Same keys as useTodaysPicks and useProps with no filters, so selecting
    // the page does not refetch a list another page already holds.
    const client = newClient()
    client.setQueryData(['games', 'today', undefined], [{ id: 1 }])
    client.setQueryData(['props', 'today', undefined, undefined], [{ id: 2 }])
    const games = vi.spyOn(api.games, 'today')
    const props = vi.spyOn(api.props, 'today')
    vi.spyOn(api.users, 'feed').mockResolvedValue([] as never)

    const { result } = renderHook(() => usePaperTradingData(), {
      wrapper: wrapper(client),
    })

    expect(result.current.games.data).toEqual([{ id: 1 }])
    expect(result.current.props.data).toEqual([{ id: 2 }])
    expect(games).not.toHaveBeenCalled()
    expect(props).not.toHaveBeenCalled()
  })

  it('maps feed rows to the shape the page renders', async () => {
    vi.spyOn(api.games, 'today').mockResolvedValue([] as never)
    vi.spyOn(api.props, 'today').mockResolvedValue([] as never)
    vi.spyOn(api.users, 'feed').mockResolvedValue([
      { event_type: 'pick_placed', payload: { message: 'Ann bet the Celtics' }, created_at: '2026-09-19T12:00:00Z' },
      { event_type: 'user_created', payload: {}, created_at: '2026-09-19T13:00:00Z' },
    ] as never)

    const { result } = renderHook(() => usePaperTradingData(), {
      wrapper: wrapper(newClient()),
    })

    await waitFor(() => expect(result.current.feed.data).toHaveLength(2))
    expect(result.current.feed.data).toEqual([
      { message: 'Ann bet the Celtics', timestamp: '2026-09-19T12:00:00Z' },
      // No message in the payload: falls back to the event type.
      { message: 'user_created', timestamp: '2026-09-19T13:00:00Z' },
    ])
  })
})
