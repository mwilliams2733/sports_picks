import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { PickData, RecordData, PropData } from '../types';
import SummaryCards from '../components/SummaryCards';
import PicksTable from '../components/PicksTable';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf'] as const;

function stars(n: number | null | undefined): string {
  if (n == null) return '-';
  return '★'.repeat(n) + '☆'.repeat(5 - n);
}

export default function TodaysPicks() {
  const [picks, setPicks] = useState<PickData[]>([]);
  const [record, setRecord] = useState<RecordData | null>(null);
  const [topProps, setTopProps] = useState<PropData[]>([]);
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

    api.props.today(sportParam).then(allProps => {
      setTopProps(allProps.filter((p: PropData) => p.confidence !== null && p.confidence >= 3)
        .sort((a: PropData, b: PropData) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))
        .slice(0, 10));
    }).catch(() => {});
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

      {topProps.length > 0 && (
        <div style={{ marginTop: '2rem' }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Top Props</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.15)', textAlign: 'left' }}>
                <th style={{ padding: '0.4rem 0.5rem' }}>Player</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Market</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Pick</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Line</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Projection</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Edge%</th>
                <th style={{ padding: '0.4rem 0.5rem' }}>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {topProps.map(p => (
                <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '0.4rem 0.5rem', fontWeight: 500 }}>{p.player_name}</td>
                  <td style={{ padding: '0.4rem 0.5rem' }}>
                    <span style={{ background: 'rgba(255,255,255,0.08)', padding: '0.1rem 0.4rem', borderRadius: '3px', fontSize: '0.8rem' }}>
                      {p.market_label}
                    </span>
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem', color: '#4ade80', fontWeight: 'bold' }}>{p.outcome}</td>
                  <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace' }}>{p.line}</td>
                  <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace' }}>{p.projection}</td>
                  <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace', color: '#4ade80' }}>
                    {p.edge_pct?.toFixed(1)}%
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem' }}>{stars(p.confidence)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
