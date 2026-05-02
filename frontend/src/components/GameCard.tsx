import type { GameOddsData, PickData } from '../types'
import ConfidenceStars from './ConfidenceStars'

interface Props {
  game: GameOddsData
  picks: PickData[]
  onBet: (pick: { pickValue: string; pickType: string; odds: number; gameId: number; edgePct?: number }) => void
}

function formatOdds(odds: number | null): string {
  if (odds == null) return '—'
  return odds >= 0 ? `+${odds}` : `${odds}`
}

export default function GameCard({ game, picks, onBet }: Props) {
  const gamePicks = picks.filter(p => p.game_id === game.id)
  const topPick = gamePicks.length > 0 ? gamePicks.reduce((a, b) => (a.confidence > b.confidence ? a : b)) : null

  return (
    <div className="game-card">
      <div className="game-card-header">
        <span className="badge badge-blue">{game.sport.toUpperCase()}</span>
        <span className="game-card-status">
          {game.status === 'final' ? `Final: ${game.home_score}-${game.away_score}` : game.status}
        </span>
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
      <div className="game-card-context">
        <span className="game-card-context-item">
          {game.home_team} {game.home_l10_record} L10
        </span>
        <span className="game-card-context-sep">·</span>
        <span className="game-card-context-item">
          {game.away_team} {game.away_l10_record} L10
        </span>
        {game.last_meeting && (
          <>
            <span className="game-card-context-sep">·</span>
            <span className="game-card-context-item">
              Last: {game.last_meeting.winner === 'tie'
                ? 'tie'
                : `${game.last_meeting.winner === 'home' ? game.home_team : game.away_team} W`}{' '}
              {Math.max(game.last_meeting.home_score, game.last_meeting.away_score)}-
              {Math.min(game.last_meeting.home_score, game.last_meeting.away_score)}
            </span>
          </>
        )}
      </div>
      {topPick && (
        <div className="game-card-pick">
          <div className="game-card-pick-info">
            <span className="game-card-pick-value">{topPick.pick_value}</span>
            <ConfidenceStars rating={topPick.confidence} />
            <span className="game-card-edge">+{topPick.edge_pct}%</span>
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
