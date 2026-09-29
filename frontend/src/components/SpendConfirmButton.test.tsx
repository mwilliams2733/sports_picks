import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SpendConfirmButton, { LAST_REFRESH_KEY } from './SpendConfirmButton'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: { credits: { get: vi.fn() } } }))

function renderIt(onConfirm = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}>
    <SpendConfirmButton label="Refresh data" pendingLabel="Refreshing…" pending={false} onConfirm={onConfirm} />
  </QueryClientProvider>)
  return onConfirm
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.credits.get).mockResolvedValue({ monthly_used: 900, monthly_limit: 20000, monthly_remaining: 19100,
    daily_used: 120, daily_target: 600, api_requests_remaining: null })
})

describe('SpendConfirmButton', () => {
  it('one tap never spends', async () => {
    const onConfirm = renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(await screen.findByText(/120 of 600 used today/)).toBeInTheDocument()
  })

  it('shows what the last refresh cost', async () => {
    localStorage.setItem(LAST_REFRESH_KEY, '34')
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    expect(await screen.findByText(/last refresh used 34 credits/)).toBeInTheDocument()
  })

  it('says the cost is unknown before the first refresh', async () => {
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    expect(await screen.findByText(/cost not known yet/)).toBeInTheDocument()
  })

  it('spends only on confirm, once', async () => {
    const onConfirm = renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Spend credits and refresh' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('cancel backs out without spending', async () => {
    const onConfirm = renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Refresh data' })).toBeInTheDocument()
  })
})
