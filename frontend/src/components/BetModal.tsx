import { useState, useEffect } from 'react'
import { useUserStore } from '../stores/userStore'
import { api } from '../api/client'
import { useQueryClient } from '@tanstack/react-query'
import { useToast } from './Toast'
import type { UserProfile } from '../types'

interface BetModalProps {
  open: boolean
  onClose: () => void
  pickValue: string
  pickType: string  // "moneyline", "spread", "over_under", "prop"
  odds: number
  gameId: number
  suggestedStake?: number
  edgePct?: number
  propMarket?: string
  propPlayer?: string
}

export default function BetModal({
  open, onClose, pickValue, pickType, odds, gameId,
  suggestedStake = 100, edgePct, propMarket, propPlayer,
}: BetModalProps) {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [stake, setStake] = useState(suggestedStake)
  const [userName, setUserName] = useState('')
  const [userId, setUserId] = useState<number | null>(null)
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

  if (!open) return null

  const handleCreateUser = async () => {
    if (!userName.trim()) return
    try {
      const res = await api.users.create(userName.trim())
      setUserId(res.id)
      setCurrentUserName(userName.trim())
      toast(`Welcome, ${userName.trim()}!`, 'success')
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : 'Failed to create user', 'error')
    }
  }

  const handlePlaceBet = async () => {
    if (!userId) return
    setSubmitting(true)
    try {
      const res = await api.users.placePick(userId, {
        game_id: gameId,
        pick_type: pickType,
        pick_value: pickValue,
        odds,
        stake,
        prop_market: propMarket,
        prop_player: propPlayer,
      })
      setResult(res)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      toast('Bet placed', 'success')
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : 'Failed to place bet', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const oddsStr = odds >= 0 ? `+${odds}` : `${odds}`

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
            <p>Enter your name to start paper trading:</p>
            <div className="bet-modal-user-setup">
              <input
                className="input"
                placeholder="Your name"
                value={userName}
                onChange={(e) => setUserName(e.target.value)}
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
                <span className="bet-detail-label">Odds</span>
                <span className="bet-detail-value">{oddsStr}</span>
              </div>
              {edgePct != null && (
                <div className="bet-detail-row">
                  <span className="bet-detail-label">Edge</span>
                  <span className="bet-detail-value" style={{ color: 'var(--green)' }}>+{edgePct}%</span>
                </div>
              )}
              <div className="bet-detail-row">
                <span className="bet-detail-label">Stake</span>
                <div className="bet-stake-input">
                  <span>$</span>
                  <input
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
            </div>
            <button
              className="btn btn-success"
              onClick={handlePlaceBet}
              disabled={submitting || stake <= 0}
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
