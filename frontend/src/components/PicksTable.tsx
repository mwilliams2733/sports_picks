import type { PickData, GameOddsData } from '../types';
import ConfidenceStars from './ConfidenceStars';
import { EDGE_TOOLTIP, CONFIDENCE_TOOLTIP } from '../constants/tooltips';
import { getGameLockState, lockStateLabel, lockStateTooltip } from '../lib/gameLock';

interface Props {
  picks: PickData[];
  showResult?: boolean;
  onBet?: (pick: PickData) => void;
  /** When provided, disables betting on picks whose game has started/ended. */
  games?: GameOddsData[];
}

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function typeLabel(type: string): string {
  if (type === 'moneyline') return 'ML';
  if (type === 'spread') return 'Spread';
  if (type === 'over_under') return 'O/U';
  if (type === 'player_prop') return 'Prop';
  return type;
}

function typeBadgeClass(type: string): string {
  if (type === 'moneyline') return 'badge badge-blue';
  if (type === 'spread') return 'badge badge-default';
  if (type === 'over_under') return 'badge badge-default';
  if (type === 'player_prop') return 'badge badge-yellow';
  return 'badge badge-default';
}

export default function PicksTable({ picks, showResult = false, onBet, games }: Props) {
  if (picks.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">No picks available</div>
        <div className="empty-state-sub">Check back once the daily pipeline has run.</div>
      </div>
    );
  }
  const gameById = new Map((games ?? []).map(g => [g.id, g]));
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Game</th>
            <th>Pick</th>
            <th>Type</th>
            <th title={EDGE_TOOLTIP}>Edge</th>
            <th title={CONFIDENCE_TOOLTIP}>Confidence</th>
            <th>Odds</th>
            {showResult && <th>Result</th>}
            {onBet && <th>Action</th>}
          </tr>
        </thead>
        <tbody>
          {picks.map(pick => {
            const game = gameById.get(pick.game_id);
            const lockState = game ? getGameLockState(game.status, game.start_time) : 'open';
            return (
            <tr key={pick.id} style={{ opacity: pick.confidence <= 1 ? 0.45 : 1 }}>
              <td>
                <span className="badge badge-default" style={{ marginRight: '0.5rem' }}>
                  {pick.sport.toUpperCase()}
                </span>
                <span className="font-medium text-primary">
                  {pick.matchup || `Game #${pick.game_id}`}
                </span>
                {lockState === 'locked' && (
                  <span className="badge badge-yellow" style={{ marginLeft: '0.5rem' }} title={lockStateTooltip(lockState)}>
                    LIVE
                  </span>
                )}
              </td>
              <td className="text-green font-bold">{pick.pick_value}</td>
              <td><span className={typeBadgeClass(pick.pick_type)}>{typeLabel(pick.pick_type)}</span></td>
              <td className="mono" style={{ color: pick.edge_pct >= 10 ? 'var(--green)' : undefined }}>
                +{pick.edge_pct}%
              </td>
              <td><ConfidenceStars rating={pick.confidence} /></td>
              <td className="mono">{formatOdds(pick.odds_at_pick)}</td>
              {showResult && (
                <td>
                  {pick.result ? (
                    <span className={`badge ${pick.result === 'win' ? 'badge-green' : pick.result === 'loss' ? 'badge-red' : 'badge-yellow'}`}>
                      {pick.result.toUpperCase()}
                      {pick.payout != null ? ` (${pick.payout > 0 ? '+' : ''}${pick.payout.toFixed(2)}u)` : ''}
                    </span>
                  ) : <span className="text-muted">Pending</span>}
                </td>
              )}
              {onBet && (
                <td>
                  <button
                    className="btn-bet"
                    disabled={lockState !== 'open'}
                    title={lockStateTooltip(lockState)}
                    onClick={() => onBet(pick)}
                  >
                    {lockStateLabel(lockState)}
                  </button>
                </td>
              )}
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
