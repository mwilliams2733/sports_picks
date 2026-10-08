import { useState, useEffect } from 'react'
import { useUserStore } from '../stores/userStore'
import { api, ApiError } from '../api/client'
import { useQueryClient } from '@tanstack/react-query'
import { useToast } from '../hooks/useToast'
import type { UserProfile } from '../types'
import { getPin, setPin as storePin } from '../lib/secrets'
import { useGameQuotes, usePropQuotes } from '../hooks/useQuotes'
import { findQuote, formatOdds, legFromPick, priceMoveNote, propOutcomeLabel, resolveLabel,
  resolveQuoteLabel } from '../lib/quotes'

interface BetModalProps {
  open: boolean
  onClose: () => void
  pickValue: string
  /** The unresolved label ("HOME ML") to key the bet off of, when it differs
   *  from the displayed pickValue (team/fighter names). Falls back to
   *  pickValue when not given -- e.g. prop labels, which already parse. */
  betValue?: string
  pickType: string  // "moneyline", "spread", "over_under", "prop"
  odds: number
  gameId: number
  suggestedStake?: number
  edgePct?: number
  propMarket?: string
  propPlayer?: string
  /** Needed to resolve a game quote's HOME/AWAY side to a real team name in
   *  the "Price now" row and the moved-line note. Unused for prop bets. */
  homeTeam?: string
  awayTeam?: string
  /** Whose price `odds` is: the model's (a model pick, the default) or one
   *  tapped on the sportsbook board. Only words the moved-price note. */
  priceSource?: 'model' | 'board'
}

export default function BetModal({
  open, onClose, pickValue, betValue, pickType, odds, gameId,
  suggestedStake = 100, edgePct, propMarket, propPlayer, homeTeam = '', awayTeam = '',
  priceSource = 'model',
}: BetModalProps) {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [stake, setStake] = useState(suggestedStake)
  const [userName, setUserName] = useState('')
  const [userId, setUserId] = useState<number | null>(null)
  const [pin, setPinInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ result: string | null; payout: number | null } | null>(null)

  // Find or create user on mount
  useEffect(() => {
    if (!open) return
    setResult(null)
    setStake(suggestedStake)
    if (currentUserName) {
      api.users.list().then((users: UserProfile[]) => {
        const found = users.find((u: UserProfile) => u.name === currentUserName)
        if (found) setUserId(found.id)
      })
    }
  }, [open, currentUserName, suggestedStake])

  // Prefill the remembered PIN once we know who the user is.
  useEffect(() => {
    if (userId != null) setPinInput(getPin(userId) ?? '')
  }, [userId])

  // Hooks run unconditionally, before the `if (!open) return null` below.
  const leg = legFromPick(pickType, betValue ?? pickValue, gameId, propMarket, propPlayer)
  const isProp = leg?.pick_type === 'prop'
  const gameQuotes = useGameQuotes(open && leg && !isProp ? gameId : null)
  const propQuotes = usePropQuotes(open && leg && isProp ? gameId : null)
  const quote = leg
    ? findQuote(gameQuotes.data?.quotes ?? [], propQuotes.data?.quotes ?? [], leg)
    : undefined
  const quotesLoading = gameQuotes.isLoading || propQuotes.isLoading

  if (!open) return null

  const handleCreateUser = async () => {
    if (!userName.trim()) return
    if (!/^\d{4,6}$/.test(pin)) {
      toast('PIN must be 4–6 digits', 'error')
      return
    }
    try {
      const res = await api.users.create(userName.trim(), pin)
      storePin(res.id, pin)
      setUserId(res.id)
      setCurrentUserName(userName.trim())
      toast(`Welcome, ${userName.trim()}!`, 'success')
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : 'Failed to create user', 'error')
    }
  }

  const handlePlaceBet = async () => {
    if (!userId || !leg || !quote?.available) return
    setSubmitting(true)
    try {
      const res = await api.users.placePick(userId, { ...leg, stake }, pin)
      setResult(res)
      storePin(userId, pin)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      // Both sides from the server's own label through the same function, so
      // an unmoved bet compares equal (a display label drops "ML" and would not).
      const label = (v: string) => isProp ? propOutcomeLabel(v) : resolveLabel(v, homeTeam, awayTeam)
      const shownLabel = label(quote.pick_value)
      const chargedLabel = label(res.pick_value)
      toast(priceMoveNote(shownLabel, quote.odds, chargedLabel, res.odds), 'success')
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 401) {
        storePin(userId, null)
        setPinInput('')
      }
      toast(e instanceof Error ? e.message : 'Failed to place bet', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal bet-modal">
        <div className="modal-header">
          <h3>Place Paper Bet</h3>
          <button className="modal-close" onClick={onClose}>&#x2715;</button>
        </div>

        {!currentUserName && !userId ? (
          <div className="modal-body">
            <label htmlFor="bet-modal-user-name">Enter your name to start paper trading:</label>
            <div className="bet-modal-user-setup">
              <input
                id="bet-modal-user-name"
                className="input"
                placeholder="Your name"
                value={userName}
                onChange={(e) => setUserName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateUser()}
              />
              <input
                aria-label="PIN"
                className="input"
                type="password"
                inputMode="numeric"
                autoComplete="off"
                maxLength={6}
                placeholder={'PIN (4–6 digits)'}
                value={pin}
                onChange={(e) => setPinInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateUser()}
              />
              <button className="btn btn-primary" onClick={handleCreateUser}>
                Start Trading
              </button>
            </div>
          </div>
        ) : result ? (
          <div className="modal-body">
            <div className={`bet-result ${result.result === 'win' ? 'win' : result.result === 'loss' ? 'loss' : ''}`}>
              {result.result ? (
                <>
                  <span className="bet-result-label">
                    {result.result === 'win' ? 'Winner!' : result.result === 'push' ? 'Push' : 'Loss'}
                  </span>
                  <span className="bet-result-payout">
                    {(result.payout || 0) >= 0 ? '+' : ''}${(result.payout || 0).toLocaleString()}
                  </span>
                </>
              ) : (
                <span className="bet-result-label">Bet placed -- pending result</span>
              )}
            </div>
            <button className="btn btn-primary" onClick={onClose} style={{ marginTop: '1rem', width: '100%' }}>
              Done
            </button>
          </div>
        ) : (
          <div className="modal-body">
            <div className="bet-modal-details">
              <div className="bet-detail-row">
                <span className="bet-detail-label">Pick</span>
                <span className="bet-detail-value">{pickValue}</span>
              </div>
              <div className="bet-detail-row">
                <span className="bet-detail-label">Price now</span>
                <span className="bet-detail-value mono">
                  {quote?.available ? (
                    <>
                      {(() => {
                        const label = resolveQuoteLabel(quote, homeTeam, awayTeam)
                        return label && <span>{label} </span>
                      })()}
                      <span>{formatOdds(quote.odds)}</span>
                    </>
                  ) : '—'}
                </span>
              </div>
              {quote?.available && quote.odds !== odds && (
                <div className="quote-reason">{priceSource === 'board'
                  ? `The price was ${formatOdds(odds)} when you tapped it.`
                  : `The model priced this at ${formatOdds(odds)}.`}</div>
              )}
              {quote?.available && !isProp && (betValue ?? pickValue) !== quote.pick_value && (
                <div className="quote-reason">
                  The line has moved from {resolveLabel(betValue ?? pickValue, homeTeam, awayTeam)} to{' '}
                  {resolveLabel(quote.pick_value, homeTeam, awayTeam)}.
                </div>
              )}
              {!leg && <div className="quote-reason">This pick can't be bet here.</div>}
              {leg && !quotesLoading && !quote && (
                <div className="quote-reason">No book is quoting this bet right now.</div>
              )}
              {quote && !quote.available && <div className="quote-reason">{quote.message}</div>}
              {edgePct != null && (
                <div className="bet-detail-row">
                  <span className="bet-detail-label">Edge</span>
                  <span className="bet-detail-value" style={{ color: 'var(--green)' }}>+{edgePct}%</span>
                </div>
              )}
              <div className="bet-detail-row">
                <label className="bet-detail-label" htmlFor="bet-modal-stake">Stake</label>
                <div className="bet-stake-input">
                  <span>$</span>
                  <input
                    id="bet-modal-stake"
                    type="number"
                    className="input"
                    value={stake}
                    onChange={(e) => setStake(Number(e.target.value))}
                    min={100}
                    max={100000}
                    step={500}
                  />
                </div>
              </div>
              <div className="bet-detail-row">
                <label className="bet-detail-label" htmlFor="bet-modal-pin">PIN</label>
                <input
                  id="bet-modal-pin"
                  type="password"
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={6}
                  className="input"
                  placeholder={'PIN (4–6 digits)'}
                  value={pin}
                  onChange={(e) => setPinInput(e.target.value)}
                />
              </div>
            </div>
            <button
              className="btn btn-success"
              onClick={handlePlaceBet}
              disabled={submitting || stake <= 0 || !quote?.available}
              style={{ width: '100%', marginTop: '1rem' }}
            >
              {submitting ? 'Placing...' : `Confirm -- $${stake.toLocaleString()}`}
            </button>
          </div>
        )}
      </div>
    </>
  )
}
