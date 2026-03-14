import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { RecordData, DailyData, PickData } from '../types';
import CalendarHeatmap from '../components/CalendarHeatmap';
import ConfidenceStars from '../components/ConfidenceStars';

export default function TrackRecord() {
  const [record, setRecord] = useState<RecordData | null>(null);
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [history, setHistory] = useState<PickData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.stats.record(), api.stats.daily(), api.picks.history()])
      .then(([r, d, h]) => { setRecord(r); setDaily(d); setHistory(h); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p>Loading...</p>;

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Track Record</h2>
      {record && (
        <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
          <Stat label="Overall Win Rate" value={`${record.win_rate}%`} color="#4ade80" />
          <Stat label="Total ROI" value={`${record.roi > 0 ? '+' : ''}${record.roi}%`} color="#4ade80" />
          <Stat label="Record" value={`${record.wins}-${record.losses}`} color="#e2e8f0" />
        </div>
      )}
      <h3 style={{ marginBottom: '0.5rem' }}>Daily Results</h3>
      <CalendarHeatmap data={daily} />
      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Pick History</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.2)', textAlign: 'left' }}>
            <th style={{ padding: '0.4rem' }}>Date</th>
            <th style={{ padding: '0.4rem' }}>Sport</th>
            <th style={{ padding: '0.4rem' }}>Pick</th>
            <th style={{ padding: '0.4rem' }}>Result</th>
            <th style={{ padding: '0.4rem' }}>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {history.map(p => (
            <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
              <td style={{ padding: '0.4rem' }}>{p.date}</td>
              <td style={{ padding: '0.4rem' }}>{p.sport.toUpperCase()}</td>
              <td style={{ padding: '0.4rem' }}>{p.pick_value}</td>
              <td style={{ padding: '0.4rem', color: p.result === 'win' ? '#4ade80' : p.result === 'loss' ? '#f87171' : '#94a3b8' }}>
                {p.result ? (p.result === 'win' ? 'Won' : p.result === 'loss' ? 'Lost' : 'Push') : 'Pending'}
              </td>
              <td style={{ padding: '0.4rem' }}><ConfidenceStars rating={p.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: '8px',
      padding: '1rem', border: '1px solid rgba(255,255,255,0.1)', textAlign: 'center' }}>
      <div style={{ fontSize: '2rem', fontWeight: 'bold', color }}>{value}</div>
      <div style={{ opacity: 0.6 }}>{label}</div>
    </div>
  );
}
