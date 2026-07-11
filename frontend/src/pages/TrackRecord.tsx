import { useSearchParams } from 'react-router-dom';
import { useRecord } from '../hooks/useRecord';
import CalendarHeatmap from '../components/CalendarHeatmap';
import PerformanceChart from '../components/PerformanceChart';
import CalibrationChart from '../components/CalibrationChart';
import ConfidenceStars from '../components/ConfidenceStars';
import { SPORTS } from '../constants/sports';

const RANGES = ['7d', '14d', '30d', '90d', 'all'] as const;

function daysFromRange(range: string): number | null {
  if (range === 'all') return null;
  return parseInt(range, 10);
}

export default function TrackRecord() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sport = searchParams.get('sport') || 'all';
  const range = searchParams.get('range') || '30d';

  const setSport = (s: string) => {
    const next: Record<string, string> = {};
    if (s !== 'all') next.sport = s;
    if (range !== '30d') next.range = range;
    setSearchParams(next);
  };

  const setRange = (r: string) => {
    const next: Record<string, string> = {};
    if (sport !== 'all') next.sport = sport;
    if (r !== '30d') next.range = r;
    setSearchParams(next);
  };

  const { record, daily, history, calibration } = useRecord(sport);

  const loading = record.isLoading || daily.isLoading || history.isLoading;
  const error = record.error || daily.error || history.error;

  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {(error as Error).message}</div></div>;
  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>;

  const recordData = record.data ?? null;
  const dailyData = daily.data ?? [];
  const historyData = history.data ?? [];

  // Filter daily data by selected range
  const days = daysFromRange(range);
  const filteredDaily = days != null
    ? dailyData.slice(-days)
    : dailyData;

  // Confidence tier breakdown
  const confidenceBreakdown = [5, 4, 3, 2, 1].map(tier => {
    const tierPicks = historyData.filter(p => p.confidence === tier && p.result);
    const wins = tierPicks.filter(p => p.result === 'win').length;
    const losses = tierPicks.filter(p => p.result === 'loss').length;
    const total = wins + losses;
    const winRate = total > 0 ? (wins / total * 100) : 0;
    const profit = tierPicks.reduce((sum, p) => sum + (p.payout || 0), 0);
    return { tier, wins, losses, total, winRate, profit };
  }).filter(b => b.total > 0);

  return (
    <div>
      {/* 1. Page header with sport tabs + range selector */}
      <div className="page-header">
        <h2 className="page-title">Track Record</h2>
      </div>

      <div className="toolbar" style={{ marginBottom: '1rem' }}>
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => setSport(s)} aria-pressed={sport === s}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="range-selector">
          {RANGES.map(r => (
            <button key={r} className={`range-btn${range === r ? ' active' : ''}`} onClick={() => setRange(r)} aria-pressed={range === r}>
              {r.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* 2. Stat cards */}
      {recordData && (
        <div className="card-grid">
          <div className="stat-card">
            <div className="stat-label">Win Rate</div>
            <div className="stat-value" style={{ color: 'var(--green)' }}>{recordData.win_rate}%</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Total ROI</div>
            <div className="stat-value" style={{ color: recordData.roi > 0 ? 'var(--green)' : 'var(--red)' }}>
              {recordData.roi > 0 ? '+' : ''}{recordData.roi}%
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Record</div>
            <div className="stat-value">{recordData.wins}-{recordData.losses}</div>
          </div>
        </div>
      )}

      {/* 3. Cumulative P&L chart */}
      <div className="section-header">Cumulative P&L <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <PerformanceChart data={filteredDaily} />
      </div>

      {/* 4. Calendar heatmap */}
      <div className="section-header">Daily Results <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <CalendarHeatmap data={filteredDaily} />
      </div>

      {/* 5. Calibration chart — predicted vs actual win rate by confidence tier */}
      <div className="section-header">Calibration <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        {calibration.data ? (
          <CalibrationChart data={calibration.data} />
        ) : (
          <div className="text-muted">Loading calibration…</div>
        )}
      </div>

      {/* 6. Confidence breakdown table */}
      {confidenceBreakdown.length > 0 && (
        <div className="confidence-breakdown">
          <div className="section-header">Confidence Breakdown <span className="section-divider" /></div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Confidence</th>
                  <th>Record</th>
                  <th>Win Rate</th>
                  <th>Profit</th>
                  <th>Sample Size</th>
                </tr>
              </thead>
              <tbody>
                {confidenceBreakdown.map(b => (
                  <tr key={b.tier}>
                    <td><ConfidenceStars rating={b.tier} /></td>
                    <td className="mono">{b.wins}-{b.losses}</td>
                    <td className={b.winRate >= 50 ? 'text-green' : 'text-red'}>
                      {b.winRate.toFixed(1)}%
                    </td>
                    <td className={`mono ${b.profit >= 0 ? 'text-green' : 'text-red'}`}>
                      {b.profit >= 0 ? '+' : ''}{b.profit.toFixed(2)}
                    </td>
                    <td className="text-muted">{b.total} picks</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 7. Pick history table */}
      <div className="section-header" style={{ marginTop: '1.5rem' }}>Pick History <span className="section-divider" /></div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Sport</th>
              <th>Pick</th>
              <th>Result</th>
              <th>CLV</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {historyData.map(p => {
              const clv = p.clv_pct ?? p.clv_points;
              const clvSuffix = p.clv_pct != null ? 'pp' : 'pts';
              return (
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
                  <td className={`mono ${clv == null ? 'text-muted' : clv > 0 ? 'text-green' : clv < 0 ? 'text-red' : ''}`}>
                    {clv == null ? '—' : `${clv > 0 ? '+' : ''}${clv.toFixed(2)} ${clvSuffix}`}
                  </td>
                  <td><ConfidenceStars rating={p.confidence} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
