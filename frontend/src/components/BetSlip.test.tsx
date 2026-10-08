import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import BetSlip from './BetSlip'
import { api, ApiError } from '../api/client'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import { useUserStore } from '../stores/userStore'
import { getPin, setPin } from '../lib/secrets'
import type { SlipLeg, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    users: { ...actual.api.users, list: vi.fn(), placePick: vi.fn(), placeParlay: vi.fn() } } }
})

const FUTURE = '2099-01-01T00:00:00+00:00'
const marcus = { id: 1, name: 'Marcus', available_balance: 1000 } as UserProfile
const ml = (game_id: number, label: string, over: Partial<SlipLeg> = {}): SlipLeg => ({
  leg: { game_id, pick_type: 'moneyline', side: 'HOME' }, label, gameLabel: `A${game_id} @ H${game_id}`,
  startTime: FUTURE, odds: -110, line: null, homeTeam: `H${game_id}`, awayTeam: `A${game_id}`, stake: 50, ...over,
})
const placed = (id: number, odds = -110) =>
  ({ id, result: null, payout: null, new_balance: 900, pick_value: 'HOME ML', odds, line: null, quoted_at: 'x' })

function renderSlip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BetSlip /></QueryClientProvider>)
}

async function enabledButton(name: string | RegExp) {
  const b = await screen.findByRole('button', { name })
  await waitFor(() => expect(b).toBeEnabled())
  return b
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  window.sessionStorage.clear()
  useSlip.setState({ ...SLIP_DEFAULTS, open: true })
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([marcus])
  setPin(1, '1234')
})

describe('BetSlip', () => {
  it('places each single at the price it showed and shows a receipt', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML', { stake: 25 })] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11)).mockResolvedValueOnce(placed(12))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByText('BET PLACED ✓')).toBeInTheDocument()
    expect(api.users.placePick).toHaveBeenNthCalledWith(1, 1, { game_id: 1, pick_type: 'moneyline', side: 'HOME',
      expected_odds: -110, expected_line: null, stake: 50 }, '1234')
    expect(screen.getByText('#P-11')).toBeInTheDocument()
    expect(screen.getByText('#P-12')).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([])
  })

  it("keeps a refused single on the slip with the server's message (Review Focus 2)", async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick)
      .mockResolvedValueOnce(placed(11))
      .mockRejectedValueOnce(new ApiError(409, { detail: 'The price is stale — ask Marcus to refresh.' }))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByText('#P-11')).toBeInTheDocument()
    expect(screen.getByText(/1 bet couldn't be placed/)).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      label: 'H2 ML', error: 'The price is stale — ask Marcus to refresh.' })])
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getByRole('alert')).toHaveTextContent('The price is stale — ask Marcus to refresh.')
  })

  it('shows a moved price and places at it on Accept & Place (Review Focus 1)', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')] })
    vi.mocked(api.users.placePick)
      .mockRejectedValueOnce(new ApiError(409, { detail: { reason: 'price_moved', message: 'm',
        odds: -125, line: null, pick_value: 'HOME ML' } }))
      .mockResolvedValueOnce(placed(11, -125))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    expect(await screen.findByText(/Odds changed -110 → -125/)).toBeInTheDocument()
    expect(screen.queryByText('BET PLACED ✓')).not.toBeInTheDocument()
    fireEvent.click(await enabledButton('Accept & Place'))
    expect(await screen.findByText('#P-11')).toBeInTheDocument()
    expect(vi.mocked(api.users.placePick).mock.calls[1][1]).toMatchObject({ expected_odds: -125 })
  })

  it('sends no expected price when any odds change is accepted', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')], acceptAnyOdds: true })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    await screen.findByText('#P-11')
    expect(vi.mocked(api.users.placePick).mock.calls[0][1])
      .toEqual({ game_id: 1, pick_type: 'moneyline', side: 'HOME', stake: 50 })
  })

  it('places a same-game parlay as one bet at the estimated price', async () => {
    const total: SlipLeg = { ...ml(7, 'Over 47.5'), leg: { game_id: 7, pick_type: 'over_under', side: 'Over' }, line: 47.5 }
    useSlip.setState({ legs: [ml(7, 'H7 ML'), total], mode: 'parlay', parlayStake: 20 })
    vi.mocked(api.users.placeParlay).mockResolvedValueOnce({ id: 4, legs: [], combined_odds: 264,
      potential_payout: 52.89, result: null, payout: null, new_balance: 980 })
    renderSlip()
    expect(screen.getByText('SGP')).toBeInTheDocument()
    expect(screen.getByText('+264')).toBeInTheDocument()
    fireEvent.click(await enabledButton('Place Parlay'))
    expect(await screen.findByText('#PL-4')).toBeInTheDocument()
    expect(api.users.placeParlay).toHaveBeenCalledWith(1, { legs: [
      { game_id: 7, pick_type: 'moneyline', side: 'HOME', expected_odds: -110, expected_line: null },
      { game_id: 7, pick_type: 'over_under', side: 'Over', expected_odds: -110, expected_line: 47.5 },
    ], stake: 20 }, '1234')
  })

  it('stops at a wrong PIN, forgets it and asks again (Review Focus 4)', async () => {
    setPin(1, '9999')
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick).mockRejectedValue(new ApiError(401, { detail: 'Wrong PIN' }))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByLabelText('PIN')).toBeInTheDocument()
    expect(api.users.placePick).toHaveBeenCalledTimes(1)
    expect(getPin(1)).toBeNull()
    expect(useSlip.getState().legs).toHaveLength(2)
  })

  it('will not place more than the available balance', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML', { stake: 2000 })] })
    renderSlip()
    expect(await screen.findByText(/Over your available balance/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Place Bet' })).toBeDisabled()
  })

  it('skips a leg whose game has started (Review Focus 3)', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML', { startTime: '2000-01-01T00:00:00Z' }), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(12))
    renderSlip()
    expect(screen.getByText('Betting closed')).toBeInTheDocument()
    fireEvent.click(await enabledButton('Place Bets'))
    await screen.findByText('#P-12')
    expect(api.users.placePick).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.users.placePick).mock.calls[0][1]).toMatchObject({ game_id: 2 })
  })

  it('Keep picks refills the slip with the placed legs', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    fireEvent.click(await screen.findByRole('button', { name: 'Keep picks' }))
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['H1 ML'])
    expect(useSlip.getState().receipt).toBeNull()
  })
})
