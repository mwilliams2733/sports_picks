import type { PickData } from '../types';
import ConfidenceStars from './ConfidenceStars';

interface Props { picks: PickData[]; }

export default function PicksTable({ picks }: Props) {
  if (picks.length === 0) {
    return <p style={{ opacity: 0.5, textAlign: 'center', padding: '2rem' }}>No picks available today.</p>;
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.2)', textAlign: 'left' }}>
          <th style={{ padding: '0.5rem' }}>Sport</th>
          <th style={{ padding: '0.5rem' }}>Pick</th>
          <th style={{ padding: '0.5rem' }}>Type</th>
          <th style={{ padding: '0.5rem' }}>Edge</th>
          <th style={{ padding: '0.5rem' }}>Confidence</th>
          <th style={{ padding: '0.5rem' }}>Odds</th>
        </tr>
      </thead>
      <tbody>
        {picks.map(pick => (
          <tr key={pick.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)',
            opacity: pick.confidence <= 1 ? 0.4 : 1 }}>
            <td style={{ padding: '0.5rem' }}>{pick.sport.toUpperCase()}</td>
            <td style={{ padding: '0.5rem', color: '#4ade80', fontWeight: 'bold' }}>{pick.pick_value}</td>
            <td style={{ padding: '0.5rem' }}>{pick.pick_type}</td>
            <td style={{ padding: '0.5rem' }}>+{pick.edge_pct}%</td>
            <td style={{ padding: '0.5rem' }}><ConfidenceStars rating={pick.confidence} /></td>
            <td style={{ padding: '0.5rem' }}>{pick.odds_at_pick > 0 ? '+' : ''}{pick.odds_at_pick}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
