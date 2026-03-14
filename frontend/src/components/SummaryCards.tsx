import type { RecordData } from '../types';

interface Props {
  record: RecordData | null;
  pickCount: number;
  strategyName: string;
}

export default function SummaryCards({ record, pickCount, strategyName }: Props) {
  return (
    <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
      <Card label="Active Strategy" value={strategyName}
        sub={record ? `${record.win_rate}% Win Rate` : '—'} color="#4ade80" />
      <Card label="Today's Picks" value={String(pickCount)}
        sub={`${pickCount} games analyzed`} color="#facc15" />
      <Card label="Season ROI" value={record ? `${record.roi > 0 ? '+' : ''}${record.roi}%` : '—'}
        sub={record ? `${record.wins}-${record.losses} Record` : '—'} color="#4ade80" />
    </div>
  );
}

function Card({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div style={{ flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: '8px',
      padding: '1rem', border: '1px solid rgba(255,255,255,0.1)' }}>
      <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', opacity: 0.6 }}>{label}</div>
      <div style={{ fontSize: '1.2rem', fontWeight: 'bold' }}>{value}</div>
      <div style={{ color }}>{sub}</div>
    </div>
  );
}
