import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { PickData, RecordData } from '../types';
import SummaryCards from '../components/SummaryCards';
import PicksTable from '../components/PicksTable';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf'] as const;

export default function TodaysPicks() {
  const [picks, setPicks] = useState<PickData[]>([]);
  const [record, setRecord] = useState<RecordData | null>(null);
  const [sport, setSport] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const sportParam = sport === 'all' ? undefined : sport;
    Promise.all([api.picks.today(sportParam), api.stats.record(sportParam)])
      .then(([p, r]) => { setPicks(p); setRecord(r); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [sport]);

  if (error) return <p style={{ color: '#f87171' }}>Error: {error}</p>;
  if (loading) return <p>Loading picks...</p>;

  return (
    <div>
      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
        {SPORTS.map(s => (
          <button key={s} onClick={() => setSport(s)} style={{
            background: sport === s ? '#1e40af' : 'transparent',
            color: sport === s ? '#fff' : '#94a3b8',
            border: '1px solid #334155', borderRadius: '6px',
            padding: '0.5rem 1rem', cursor: 'pointer' }}>
            {s.toUpperCase()}
          </button>
        ))}
      </div>
      <SummaryCards record={record} pickCount={picks.length} strategyName="Ensemble" />
      <PicksTable picks={picks} />
    </div>
  );
}
