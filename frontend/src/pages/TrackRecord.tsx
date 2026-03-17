import { useSearchParams } from 'react-router-dom';
import { useRecord } from '../hooks/useRecord';
import CalendarHeatmap from '../components/CalendarHeatmap';
import ConfidenceStars from '../components/ConfidenceStars';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'boxing', 'mma'] as const;

export default function TrackRecord() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sport = searchParams.get('sport') || 'all';

  const setSport = (s: string) => {
    if (s === 'all') {
      setSearchParams({});
    } else {
      setSearchParams({ sport: s });
    }
  };

  const { record, daily, history } = useRecord(sport);

  const loading = record.isLoading || daily.isLoading || history.isLoading;
  const error = record.error || daily.error || history.error;

  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {(error as Error).message}</div></div>;
  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>;

  const recordData = record.data ?? null;
  const dailyData = daily.data ?? [];
  const historyData = history.data ?? [];

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Track Record</h2>
      </div>

      <div className="toolbar" style={{ marginBottom: '1rem' }}>
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => setSport(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

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

      <div className="section-header">Daily Results <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <CalendarHeatmap data={dailyData} />
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
            {historyData.map(p => (
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
