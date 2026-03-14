import type { PickData } from '../types';
import ConfidenceStars from './ConfidenceStars';

interface Props { picks: PickData[]; showResult?: boolean; }

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

export default function PicksTable({ picks, showResult = false }: Props) {
  if (picks.length === 0) {
    return <p style={{ opacity: 0.5, textAlign: 'center', padding: '2rem' }}>No picks available.</p>;
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
      <thead>
        <tr style={{ borderBottom: '2px solid rgba(255,255,255,0.15)', textAlign: 'left' }}>
          <th style={{ padding: '0.6rem 0.5rem' }}>Game</th>
          <th style={{ padding: '0.6rem 0.5rem' }}>Pick</th>
          <th style={{ padding: '0.6rem 0.5rem' }}>Type</th>
          <th style={{ padding: '0.6rem 0.5rem' }}>Edge</th>
          <th style={{ padding: '0.6rem 0.5rem' }}>Confidence</th>
          <th style={{ padding: '0.6rem 0.5rem' }}>Odds</th>
          {showResult && <th style={{ padding: '0.6rem 0.5rem' }}>Result</th>}
        </tr>
      </thead>
      <tbody>
        {picks.map(pick => (
          <tr key={pick.id} style={{
            borderBottom: '1px solid rgba(255,255,255,0.05)',
            opacity: pick.confidence <= 1 ? 0.45 : 1,
          }}>
            <td style={{ padding: '0.6rem 0.5rem' }}>
              <span style={{ color: '#94a3b8', fontSize: '0.75rem', marginRight: '0.4rem' }}>
                {pick.sport.toUpperCase()}
              </span>
              <span style={{ fontWeight: 500 }}>{pick.matchup || `Game #${pick.game_id}`}</span>
            </td>
            <td style={{ padding: '0.6rem 0.5rem', color: '#4ade80', fontWeight: 'bold' }}>
              {pick.pick_value}
            </td>
            <td style={{ padding: '0.6rem 0.5rem' }}>
              <span style={{
                background: 'rgba(255,255,255,0.08)', padding: '0.15rem 0.5rem',
                borderRadius: '4px', fontSize: '0.8rem',
              }}>{typeLabel(pick.pick_type)}</span>
            </td>
            <td style={{ padding: '0.6rem 0.5rem', color: pick.edge_pct >= 10 ? '#4ade80' : '#e2e8f0' }}>
              +{pick.edge_pct}%
            </td>
            <td style={{ padding: '0.6rem 0.5rem' }}><ConfidenceStars rating={pick.confidence} /></td>
            <td style={{ padding: '0.6rem 0.5rem', fontFamily: 'monospace' }}>
              {formatOdds(pick.odds_at_pick)}
            </td>
            {showResult && (
              <td style={{ padding: '0.6rem 0.5rem' }}>
                {pick.result ? (
                  <span style={{
                    color: pick.result === 'win' ? '#4ade80' : pick.result === 'loss' ? '#f87171' : '#fbbf24',
                    fontWeight: 'bold',
                  }}>{pick.result.toUpperCase()}{pick.payout != null ? ` (${pick.payout > 0 ? '+' : ''}${pick.payout.toFixed(2)}u)` : ''}</span>
                ) : <span style={{ opacity: 0.4 }}>Pending</span>}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
