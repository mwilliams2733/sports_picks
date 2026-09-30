import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import Admin from './Admin'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import type { UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { users: { list: vi.fn(), delete: vi.fn(), setPin: vi.fn() } } }
})

const user = (id: number, name: string, has_pin: boolean): UserProfile => ({
  id, name, has_pin, starting_balance: 10000, current_balance: 10000, total_wagered: 0, profit: 0,
  roi: 0, wins: 0, losses: 0, pushes: 0, pending: 0, win_rate: 0, current_streak: 0,
  best_streak: 0, streak_type: 'none',
})

describe('Admin', () => {
  it('shows No PIN exactly where a player has none', async () => {
    vi.mocked(api.users.list).mockResolvedValue([user(1, 'Marcus', false), user(3, 'Sam', true)])
    render(<ToastProvider><Admin /></ToastProvider>)
    expect(await screen.findByText('Marcus')).toBeInTheDocument()
    expect(screen.getAllByText('No PIN')).toHaveLength(1)
    expect(screen.getByText('Marcus').closest('td')).toHaveTextContent('No PIN')
    expect(screen.getAllByRole('button', { name: 'Set PIN' })).toHaveLength(2)
  })
})
