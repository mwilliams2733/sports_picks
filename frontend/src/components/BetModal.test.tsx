import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import BetModal from './BetModal'
import { ToastProvider } from './Toast'
import { useUserStore } from '../stores/userStore'
import { api, ApiError } from '../api/client'
import type { UserProfile } from '../types'
import { getPin } from '../lib/secrets'

function makeUser(overrides: Partial<UserProfile> = {}): UserProfile {
  return {
    id: 1, name: 'Marcus', starting_balance: 10000, current_balance: 10000, available_balance: 10000,
    total_wagered: 0, profit: 0, roi: 0, wins: 0, losses: 0, pushes: 0,
    pending: 0, win_rate: 0, current_streak: 0, best_streak: 0, streak_type: 'none',
    ...overrides,
  }
}

vi.mock('../api/client', async (importOriginal) => {
  // Keep the real ApiError (its `detail` parsing is what BetModal relies on
  // to tell a wrong-PIN 401 apart from other errors); only the network calls
  // are mocked.
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      users: {
        list: vi.fn(),
        create: vi.fn(),
        placePick: vi.fn(),
      },
      paper: {
        quotes: vi.fn(),
        propQuotes: vi.fn(),
      },
    },
  }
})

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
    window.sessionStorage.clear()
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -140,
        line: null, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })
    vi.mocked(api.paper.propQuotes).mockResolvedValue({ game_id: 1, quotes: [] })
  })

  it('prompts for a name when there is no current user', () => {
    renderModal()
    expect(screen.getByLabelText(/Enter your name/i)).toBeInTheDocument()
  })

  it('joins with a PIN, and keeps it in the field afterward', async () => {
    vi.mocked(api.users.create).mockResolvedValue({ id: 7, name: 'Sam' })
    vi.mocked(api.users.list).mockResolvedValue([])
    const user = userEvent.setup()
    renderModal()

    await user.type(screen.getByPlaceholderText('Your name'), 'Sam')
    await user.type(screen.getByLabelText('PIN'), '4321')
    await user.click(screen.getByRole('button', { name: 'Start Trading' }))

    expect(api.users.create).toHaveBeenCalledWith('Sam', '4321')
    // The prefill effect fires once userId resolves; it must read back the
    // PIN we just stored, not blank the field the player just filled in.
    await waitFor(() => expect(getPin(7)).toBe('4321'))
    expect(await screen.findByLabelText('PIN')).toHaveValue('4321')
  })

  it('rejects a malformed PIN before joining', async () => {
    const user = userEvent.setup()
    renderModal()

    await user.type(screen.getByPlaceholderText('Your name'), 'Sam')
    await user.type(screen.getByLabelText('PIN'), '12')
    await user.click(screen.getByRole('button', { name: 'Start Trading' }))

    expect(await screen.findByText('PIN must be 4–6 digits')).toBeInTheDocument()
    expect(api.users.create).not.toHaveBeenCalled()
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
    type PlacePickResult = { id: number; result: string | null; payout: number | null; new_balance: number; pick_value: string; odds: number; line: number | null; quoted_at: string }
    let resolvePlacePick: (value: PlacePickResult) => void = () => {}
    vi.mocked(api.users.placePick).mockReturnValue(
      new Promise<PlacePickResult>((resolve) => { resolvePlacePick = resolve })
    )

    const user = userEvent.setup()
    renderModal()

    await user.type(await screen.findByLabelText('PIN'), '1234')
    const confirmButton = await screen.findByRole('button', { name: /Confirm/ })
    await user.click(confirmButton)

    expect(screen.getByRole('button', { name: /Placing/ })).toBeDisabled()
    expect(api.users.placePick).toHaveBeenCalledWith(
      1,
      { game_id: 1, pick_type: 'moneyline', side: 'HOME', stake: 100 },
      '1234'
    )

    resolvePlacePick({ id: 1, result: null, payout: null, new_balance: 9900, pick_value: 'HOME ML', odds: -140, line: null, quoted_at: '' })
    await waitFor(() => expect(screen.getByText(/pending result/i)).toBeInTheDocument())
  })

  it('shows the current price and notes when it differs from the model price', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    renderModal()          // model price -150, quote -140
    expect(await screen.findByText('-140')).toBeInTheDocument()
    expect(screen.getByText(/model priced this at -150/i)).toBeInTheDocument()
  })

  it('shows the current team-resolved line and notes when the line has moved (Review Focus: a line move used to be charged silently)', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'spread', side: 'AWAY', available: true, pick_value: 'AWAY +2.5', odds: -115,
        line: 2.5, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })

    renderModal({
      pickValue: 'BOS +3.5', betValue: 'AWAY +3.5', pickType: 'spread', odds: -110,
      homeTeam: 'LAL', awayTeam: 'BOS',
    })

    // Price now shows the CURRENT (quoted) line, team-resolved, not the
    // model's stale +3.5.
    expect(await screen.findByText('-115')).toBeInTheDocument()
    expect(screen.getByText('BOS +2.5')).toBeInTheDocument()
    expect(screen.getByText('The line has moved from BOS +3.5 to BOS +2.5.')).toBeInTheDocument()
  })

  it('reports no move after placing a moneyline bet at the price shown', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.users.placePick).mockResolvedValue(
      { id: 1, result: null, payout: null, new_balance: 9900, pick_value: 'HOME ML', odds: -140, line: null, quoted_at: '' })
    const user = userEvent.setup()
    renderModal({ homeTeam: 'LAL', awayTeam: 'BOS' })   // quote HOME ML -140

    await user.type(await screen.findByLabelText('PIN'), '1234')
    await user.click(await screen.findByRole('button', { name: /Confirm/ }))

    expect(await screen.findByText('Placed at LAL ML -140')).toBeInTheDocument()
    expect(screen.queryByText(/when you looked/)).not.toBeInTheDocument()
  })

  it('reports the move when the server charges a different price', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.users.placePick).mockResolvedValue(
      { id: 1, result: null, payout: null, new_balance: 9900, pick_value: 'HOME ML', odds: -150, line: null, quoted_at: '' })
    const user = userEvent.setup()
    renderModal({ homeTeam: 'LAL', awayTeam: 'BOS' })   // quote HOME ML -140

    await user.type(await screen.findByLabelText('PIN'), '1234')
    await user.click(await screen.findByRole('button', { name: /Confirm/ }))

    expect(await screen.findByText('Placed at LAL ML -150 — was LAL ML -140 when you looked')).toBeInTheDocument()
  })

  it('cannot confirm a pick the server will not price', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: false, reason: 'stale',
        message: 'The price is stale — ask Marcus to refresh.' },
    ] })
    renderModal()
    expect(await screen.findByText('The price is stale — ask Marcus to refresh.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm/ })).toBeDisabled()
  })

  it('forgets the PIN and clears the field on a wrong-PIN 401', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.users.placePick).mockRejectedValue(new ApiError(401, { detail: 'Wrong PIN' }))

    const user = userEvent.setup()
    renderModal()

    const pinInput = await screen.findByLabelText('PIN')
    await user.type(pinInput, '1234')
    await user.click(await screen.findByRole('button', { name: /Confirm/ }))

    expect(await screen.findByText('Wrong PIN')).toBeInTheDocument()
    expect(getPin(1)).toBe(null)
    expect(pinInput).toHaveValue('')
  })
})
