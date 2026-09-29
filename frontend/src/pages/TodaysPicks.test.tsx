import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TodaysPicks from './TodaysPicks'
import { setOwnerKey } from '../lib/secrets'

const ok = <T,>(data: T) => ({ data, error: null, isLoading: false })

vi.mock('../hooks/useTodaysPicks', () => ({
  useTodaysPicks: () => ({ picks: ok([]), record: ok(null), games: ok([]) }),
}))
vi.mock('../hooks/useTopProps', () => ({ useTopProps: () => ok([]) }))
vi.mock('../hooks/useRefreshData', () => ({
  useRefreshData: () => ({ isPending: false, mutate: vi.fn() }),
}))
vi.mock('../components/CreditUsage', () => ({ default: () => null }))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={['/']}><TodaysPicks /></MemoryRouter></QueryClientProvider>)
}

describe('TodaysPicks refresh button', () => {
  afterEach(() => setOwnerKey(null))

  it('is hidden from a friend: no owner key in this browser', () => {
    setOwnerKey(null)
    renderPage()
    expect(screen.queryByRole('button', { name: /Refresh data/ })).toBeNull()
  })

  it('is shown to the owner', () => {
    setOwnerKey('owner-key')
    renderPage()
    expect(screen.getByRole('button', { name: /Refresh data/ })).toBeInTheDocument()
  })
})
