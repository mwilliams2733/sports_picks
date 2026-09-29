import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { ComponentProps } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SpendConfirmButton from './SpendConfirmButton'
import { LAST_REFRESH_KEY } from '../lib/refreshCost'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: { credits: { get: vi.fn() } } }))

function renderIt(onConfirm = vi.fn(), props: Partial<ComponentProps<typeof SpendConfirmButton>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}>
    <SpendConfirmButton label="Refresh data" pendingLabel="Refreshing…" pending={false} onConfirm={onConfirm} {...props} />
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

  it('treats junk stored cost as unknown, never NaN', async () => {
    localStorage.setItem(LAST_REFRESH_KEY, 'junk')
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh data' }))
    expect(await screen.findByText(/cost not known yet/)).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
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

  it('applies a custom className to the idle button', () => {
    renderIt(vi.fn(), { className: 'btn btn-primary btn-sm' })
    expect(screen.getByRole('button', { name: 'Refresh data' })).toHaveClass('btn', 'btn-primary', 'btn-sm')
  })

  it('shows a spinner while pending when showSpinner is set', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(<QueryClientProvider client={qc}>
      <SpendConfirmButton label="Run Now" pendingLabel="Running…" pending onConfirm={vi.fn()} showSpinner />
    </QueryClientProvider>)
    expect(screen.getByRole('button', { name: /Running…/ })).toBeDisabled()
    expect(container.querySelector('.spinner')).toBeInTheDocument()
  })
})
