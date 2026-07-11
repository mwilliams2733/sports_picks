import type { GameOddsData, PickData } from '../types'
import ConfidenceStars from './ConfidenceStars'
import { EDGE_TOOLTIP } from '../constants/tooltips'

interface Props {
  game: GameOddsData
  picks: PickData[]
  onBet: (pick: { pickValue: string; pickType: string; odds: number; gameId: number; edgePct?: number }) => void
  onHide?: (gameId: number) => void
}

function formatOdds(odds: number | null): string {
  if (odds == null) return '—'
  return odds >= 0 ? `+${odds}` : `${odds}`
}

function formatMeetingDate(iso: string): string {
  // "2026-03-02" → "Mar 2"
  const [, m, d] = iso.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const monthName = months[Math.max(0, Math.min(11, parseInt(m, 10) - 1))]
  return `${monthName} ${parseInt(d, 10)}`
}

export default function GameCard({ game, picks, onBet, onHide }: Props) {
  const gamePicks = picks.filter(p => p.game_id === game.id)
  const topPick = gamePicks.length > 0 ? gamePicks.reduce((a, b) => (a.confidence > b.confidence ? a : b)) : null

  // For "BOS won 114-98" we need the higher score first regardless of which
  // corner the team was in during the prior meeting.
  let lastMeetingLabel: string | null = null
  if (game.last_meeting) {
    const lm = game.last_meeting
    const high = Math.max(lm.home_score, lm.away_score)
    const low = Math.min(lm.home_score, lm.away_score)
    if (lm.winner === 'tie') {
      lastMeetingLabel = `tied ${high}-${low}`
    } else {
      const winnerAbbr = lm.winner === 'home' ? game.home_team : game.away_team
      lastMeetingLabel = `${winnerAbbr} won ${high}-${low}`
    }
  }

  return (
    <div className="game-card">
      <div className="game-card-header">
        <span className="badge badge-blue">{game.sport.toUpperCase()}</span>
        <span className="game-card-status">
          {game.status === 'final' ? `Final: ${game.home_score}-${game.away_score}` : game.status}
        </span>
        {onHide && (
          <button
            className="game-card-hide"
            onClick={() => onHide(game.id)}
            title="Hide this game"
            aria-label="Hide this game"
            style={{
              marginLeft: 'auto',
              background: 'transparent',
              border: 'none',
              color: 'var(--text-muted, #94a3b8)',
              cursor: 'pointer',
              fontSize: '1.1rem',
              lineHeight: 1,
              padding: '0 0.25rem',
            }}
          >
            ×
          </button>
        )}
      </div>
      <div className="game-card-matchup">
        <div className="game-card-team">
          <span className="game-card-team-name">{game.home_team}</span>
          <span className="game-card-odds">{formatOdds(game.moneyline_home)}</span>
        </div>
        <span className="game-card-vs">vs</span>
        <div className="game-card-team">
          <span className="game-card-team-name">{game.away_team}</span>
          <span className="game-card-odds">{formatOdds(game.moneyline_away)}</span>
        </div>
      </div>
      <div className="game-card-lines">
        {game.spread_home != null && (
          <span className="game-card-line">Spread: {game.spread_home > 0 ? '+' : ''}{game.spread_home}</span>
        )}
        {game.over_under != null && (
          <span className="game-card-line">O/U: {game.over_under}</span>
        )}
      </div>
      <div className="game-card-context" style={{
        marginTop: '0.5rem',
        fontSize: '0.85rem',
        color: 'var(--text-muted, #94a3b8)',
        lineHeight: 1.45,
      }}>
        <div>
          Last 10: <span className="font-medium">{game.home_team} {game.home_l10_record}</span>
          {' · '}
          <span className="font-medium">{game.away_team} {game.away_l10_record}</span>
        </div>
        {lastMeetingLabel && game.last_meeting && (
          <div>
            Last meeting: {lastMeetingLabel}
            {' '}
            <span style={{ opacity: 0.75 }}>({formatMeetingDate(game.last_meeting.date)})</span>
          </div>
        )}
      </div>
      {topPick && (
        <div className="game-card-pick">
          <div className="game-card-pick-info">
            <span className="game-card-pick-value">{topPick.pick_value}</span>
            <ConfidenceStars rating={topPick.confidence} />
            <span className="game-card-edge" title={EDGE_TOOLTIP}>+{topPick.edge_pct}%</span>
          </div>
          <button
            className="btn-bet"
            onClick={() => onBet({
              pickValue: topPick.pick_value,
              pickType: topPick.pick_type,
              odds: topPick.odds_at_pick,
              gameId: topPick.game_id,
              edgePct: topPick.edge_pct,
            })}
          >
            Bet This
          </button>
        </div>
      )}
    </div>
  )
}
