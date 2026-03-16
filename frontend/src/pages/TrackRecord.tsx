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

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>;

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Track Record</h2>
      </div>

      {record && (
        <div className="card-grid">
          <div className="stat-card">
            <div className="stat-label">Win Rate</div>
            <div className="stat-value" style={{ color: 'var(--green)' }}>{record.win_rate}%</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Total ROI</div>
            <div className="stat-value" style={{ color: record.roi > 0 ? 'var(--green)' : 'var(--red)' }}>
              {record.roi > 0 ? '+' : ''}{record.roi}%
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Record</div>
            <div className="stat-value">{record.wins}-{record.losses}</div>
          </div>
        </div>
      )}

      <div className="section-header">Daily Results <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <CalendarHeatmap data={daily} />
      </div>

      <div className="section-header">Pick History <span className="section-divider" /></div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Sport</th>
              <th>Pick</th>
              <th>Result</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {history.map(p => (
              <tr key={p.id}>
                <td className="mono">{p.date}</td>
                <td><span className="badge badge-default">{p.sport.toUpperCase()}</span></td>
                <td className="font-medium text-primary">{p.pick_value}</td>
                <td>
                  {p.result ? (
                    <span className={`badge ${p.result === 'win' ? 'badge-green' : p.result === 'loss' ? 'badge-red' : 'badge-yellow'}`}>
                      {p.result === 'win' ? 'Won' : p.result === 'loss' ? 'Lost' : 'Push'}
                    </span>
                  ) : <span className="text-muted">Pending</span>}
                </td>
                <td><ConfidenceStars rating={p.confidence} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
