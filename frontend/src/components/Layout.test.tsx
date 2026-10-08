import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Layout from './Layout'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { getPin } from '../lib/secrets'
import type { UserProfile } from '../types'

vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => {} }))
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn(), create: vi.fn() } } }
})

const u = (id: number, name: string, available_balance: number) =>
  ({ id, name, available_balance, starting_balance: 10000, current_balance: 10000 }) as UserProfile

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/" element={<Layout />}>
      <Route index element={<p>lobby page</p>} />
      <Route path="model-picks" element={<p>picks page</p>} />
    </Route></Routes></MemoryRouter></QueryClientProvider>)
}

describe('Layout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.users.list).mockResolvedValue([u(1, 'Marcus', 10240.5), u(2, 'Sam', 9800)])
    useUserStore.getState().setCurrentUserName('Marcus')
  })

  it('has Lobby and My Bets tabs and the METRIC EDGE wordmark', () => {
    renderAt('/')
    expect(screen.getByText('lobby page')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /Lobby/ })[0]).toHaveAttribute('href', '/')
    expect(screen.getAllByRole('link', { name: /My Bets/ })[0]).toHaveAttribute('href', '/paper-trading')
    expect(screen.getByRole('link', { name: /METRIC EDGE/ })).toBeInTheDocument()
  })

  it('opens the Research menu with Model Picks', () => {
    renderAt('/')
    fireEvent.click(screen.getAllByRole('button', { name: /Research/ })[0])
    expect(screen.getByRole('link', { name: 'Model Picks' })).toHaveAttribute('href', '/model-picks')
  })

  it('shows the sport filter only on research pages', () => {
    const { unmount } = renderAt('/')
    expect(screen.queryByLabelText('Sport filter')).not.toBeInTheDocument()
    unmount()
    renderAt('/model-picks')
    expect(screen.getByLabelText('Sport filter')).toBeInTheDocument()
  })

  it("shows the player's available balance and switches player", async () => {
    renderAt('/')
    const chip = await screen.findByRole('button', { name: 'Marcus · $10,240.50' })
    fireEvent.click(chip)
    fireEvent.click(screen.getByRole('button', { name: /Sam/ }))
    expect(useUserStore.getState().currentUserName).toBe('Sam')
  })

  it('joins the league from the chip and becomes the current player', async () => {
    vi.mocked(api.users.create).mockResolvedValue({ id: 3, name: 'Jo' })
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: 'Marcus · $10,240.50' }))
    fireEvent.click(screen.getByRole('button', { name: /Join the league/ }))
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Jo' } })
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '4321' } })
    fireEvent.click(screen.getByRole('button', { name: 'Join' }))
    await waitFor(() => expect(useUserStore.getState().currentUserName).toBe('Jo'))
    expect(api.users.create).toHaveBeenCalledWith('Jo', '4321')
    expect(getPin(3)).toBe('4321')
  })

  it('refuses a malformed PIN before calling the server', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: 'Marcus · $10,240.50' }))
    fireEvent.click(screen.getByRole('button', { name: /Join the league/ }))
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Jo' } })
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Join' }))
    expect(screen.getByRole('alert')).toHaveTextContent('PIN must be 4–6 digits.')
    expect(api.users.create).not.toHaveBeenCalled()
  })
})
