import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import CashOutButton from './CashOutButton'
import { api, ApiError } from '../api/client'
import { getPin, setPin } from '../lib/secrets'
import type { Ticket } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, cashOut: vi.fn() } } }
})

const ticket = (over: Partial<Ticket> = {}): Ticket => ({
  kind: 'straight', id: 6, stake: 100, odds: -110, to_win: 90.91, result: null, payout: null,
  created_at: '2026-10-09T12:00:00+00:00', sgp: false, legs: [],
  cash_out: { available: true, offer: 90.68 }, ...over,
})

function renderButton(t: Ticket) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(<QueryClientProvider client={client}><CashOutButton t={t} userId={1} /></QueryClientProvider>)
  return spy
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  window.sessionStorage.clear()
  setPin(1, '1234')
})

describe('CashOutButton', () => {
  it('shows the offer, asks to confirm, then cashes out at it', async () => {
    vi.mocked(api.users.cashOut).mockResolvedValue({ bet_id: 6, kind: 'straight', offer: 90.68, payout: -9.32, available: 9990.68 })
    const invalidate = renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    expect(api.users.cashOut).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    await waitFor(() => expect(api.users.cashOut).toHaveBeenCalledWith(1,
      { bet_id: 6, kind: 'straight', expected_offer: 90.68 }, '1234'))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['users'] }))
  })
  it('greys the button with the reason when there is no offer', () => {
    renderButton(ticket({ cash_out: { available: false, reason: 'line_moved',
      message: "The line has moved since you bet, so there's no cash out offer." } }))
    expect(screen.getByRole('button', { name: 'Cash out' })).toBeDisabled()
    expect(screen.getByText("The line has moved since you bet, so there's no cash out offer.")).toBeInTheDocument()
  })
  it('asks again at the new offer when it changed (Review Focus 3)', async () => {
    vi.mocked(api.users.cashOut)
      .mockRejectedValueOnce(new ApiError(409, { detail: { reason: 'offer_changed', offer: 85, message: 'The offer changed to $85.00.' } }))
      .mockResolvedValueOnce({ bet_id: 6, kind: 'straight', offer: 85, payout: -15, available: 9985 })
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    expect(await screen.findByText('The offer changed to $85.00.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    await waitFor(() => expect(api.users.cashOut).toHaveBeenLastCalledWith(1,
      { bet_id: 6, kind: 'straight', expected_offer: 85 }, '1234'))
  })
  it('forgets a wrong PIN and asks for it', async () => {
    vi.mocked(api.users.cashOut).mockRejectedValueOnce(new ApiError(401, { detail: 'Wrong PIN' }))
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    expect(await screen.findByLabelText('PIN')).toBeInTheDocument()
    expect(getPin(1)).toBeNull()
  })
  it('needs a PIN before it can confirm when none is saved', () => {
    setPin(1, null)
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    expect(screen.getByRole('button', { name: 'Confirm cash out' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '1234' } })
    expect(screen.getByRole('button', { name: 'Confirm cash out' })).toBeEnabled()
  })
  it('Keep bet backs out without cashing out', () => {
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep bet' }))
    expect(screen.getByRole('button', { name: 'Cash out $90.68' })).toBeInTheDocument()
    expect(api.users.cashOut).not.toHaveBeenCalled()
  })
})
