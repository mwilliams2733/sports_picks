import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SetPinControl from './SetPinControl'
import { ToastProvider } from './Toast'
import { api, ApiError } from '../api/client'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { users: { setPin: vi.fn() } } }
})

function renderControl(hasOwnerKey = true, onSaved = vi.fn()) {
  render(<ToastProvider><SetPinControl userId={2} userName="Demo" hasOwnerKey={hasOwnerKey} onSaved={onSaved} /></ToastProvider>)
  return onSaved
}

describe('SetPinControl', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the PIN only on Save, exactly as typed', async () => {
    vi.mocked(api.users.setPin).mockResolvedValue({ id: 2, pin_set: true })
    const user = userEvent.setup()
    const onSaved = renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '0123')
    expect(api.users.setPin).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.users.setPin).toHaveBeenCalledWith(2, '0123')
    expect(onSaved).toHaveBeenCalled()
    expect(screen.queryByLabelText('New PIN for Demo')).toBeNull()
  })

  it('will not save a malformed PIN', async () => {
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '12')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('asks for the owner key when none is saved', async () => {
    const user = userEvent.setup()
    renderControl(false)
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    expect(screen.getByText(/Save the owner key above first/)).toBeInTheDocument()
    expect(screen.queryByLabelText('New PIN for Demo')).toBeNull()
  })

  it('reports a rejected owner key', async () => {
    vi.mocked(api.users.setPin).mockRejectedValue(new ApiError(403, { detail: 'Forbidden' }))
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '4321')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Owner key required/)).toBeInTheDocument()
  })

  it('Cancel closes without sending', async () => {
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '4321')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.users.setPin).not.toHaveBeenCalled()
  })
})
