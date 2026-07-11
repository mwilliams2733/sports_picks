import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import BetModal from './BetModal'
import { ToastProvider } from './Toast'
import { useUserStore } from '../stores/userStore'
import { api } from '../api/client'
import type { UserProfile } from '../types'

function makeUser(overrides: Partial<UserProfile> = {}): UserProfile {
  return {
    id: 1, name: 'Marcus', starting_balance: 10000, current_balance: 10000,
    total_wagered: 0, profit: 0, roi: 0, wins: 0, losses: 0, pushes: 0,
    pending: 0, win_rate: 0, current_streak: 0, best_streak: 0, streak_type: 'none',
    ...overrides,
  }
}

vi.mock('../api/client', () => ({
  api: {
    users: {
      list: vi.fn(),
      create: vi.fn(),
      placePick: vi.fn(),
    },
  },
}))

function renderModal(props: Partial<React.ComponentProps<typeof BetModal>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BetModal
          open
          onClose={vi.fn()}
          pickValue="HOME ML"
          pickType="moneyline"
          odds={-150}
          gameId={1}
          {...props}
        />
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('BetModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUserStore.setState({ currentUserName: null, selectedUser: null })
    window.localStorage.clear()
  })

  it('prompts for a name when there is no current user', () => {
    renderModal()
    expect(screen.getByLabelText(/Enter your name/i)).toBeInTheDocument()
  })

  it('shows the bet details and stake input once a user exists', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])

    renderModal()

    await waitFor(() => expect(api.users.list).toHaveBeenCalled())
    expect(await screen.findByText('HOME ML')).toBeInTheDocument()
    expect(screen.getByLabelText('Stake')).toBeInTheDocument()
  })

  it('disables the submit button while the bet request is in flight', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    type PlacePickResult = { id: number; result: string | null; payout: number | null; new_balance: number }
    let resolvePlacePick: (value: PlacePickResult) => void = () => {}
    vi.mocked(api.users.placePick).mockReturnValue(
      new Promise<PlacePickResult>((resolve) => { resolvePlacePick = resolve })
    )

    const user = userEvent.setup()
    renderModal()

    const confirmButton = await screen.findByRole('button', { name: /Confirm/ })
    await user.click(confirmButton)

    expect(screen.getByRole('button', { name: /Placing/ })).toBeDisabled()

    resolvePlacePick({ id: 1, result: null, payout: null, new_balance: 9900 })
    await waitFor(() => expect(screen.getByText(/pending result/i)).toBeInTheDocument())
  })
})
