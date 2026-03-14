import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { PropData } from '../types';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf'] as const;

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function oddsColor(odds: number): string {
  return odds > 0 ? '#4ade80' : '#e2e8f0';
}

function stars(n: number | null | undefined): string {
  if (n == null) return '-';
  return '★'.repeat(n) + '☆'.repeat(5 - n);
}

export default function PlayerProps() {
  const [props, setProps] = useState<PropData[]>([]);
  const [sport, setSport] = useState<string>('all');
  const [market, setMarket] = useState<string>('');
  const [markets, setMarkets] = useState<{ key: string; label: string }[]>([]);
  const [minConfidence, setMinConfidence] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.props.markets().then(setMarkets).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const sportParam = sport === 'all' ? undefined : sport;
    const marketParam = market || undefined;
    api.props.today(sportParam, marketParam)
      .then(setProps)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [sport, market]);

  // Filter and sort props before grouping
  const filtered = props
    .filter(p => p.confidence === null || p.confidence >= minConfidence)
    .sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0));

  // Group props by game, then by player
  const grouped = filtered.reduce<Record<string, Record<string, PropData[]>>>((acc, p) => {
    const key = p.matchup;
    if (!acc[key]) acc[key] = {};
    if (!acc[key][p.player_name]) acc[key][p.player_name] = [];
    acc[key][p.player_name].push(p);
    return acc;
  }, {});

  if (error) return <p style={{ color: '#f87171' }}>Error: {error}</p>;

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Player Props</h2>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        {SPORTS.map(s => (
          <button key={s} onClick={() => setSport(s)} style={{
            background: sport === s ? '#1e40af' : 'transparent',
            color: sport === s ? '#fff' : '#94a3b8',
            border: '1px solid #334155', borderRadius: '6px',
            padding: '0.5rem 1rem', cursor: 'pointer',
          }}>{s.toUpperCase()}</button>
        ))}

        <select value={market} onChange={e => setMarket(e.target.value)} style={{
          background: '#1e1e1e', color: '#e0e0e0', border: '1px solid #334155',
          borderRadius: '6px', padding: '0.5rem', marginLeft: 'auto',
        }}>
          <option value="">All Markets</option>
          {markets.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>

        <select value={minConfidence} onChange={e => setMinConfidence(Number(e.target.value))} style={{
          background: '#1e1e1e', color: '#e0e0e0', border: '1px solid #334155',
          borderRadius: '6px', padding: '0.5rem',
        }}>
          <option value={0}>All Confidence</option>
          <option value={1}>1+ Stars</option>
          <option value={2}>2+ Stars</option>
          <option value={3}>3+ Stars</option>
          <option value={4}>4+ Stars</option>
          <option value={5}>5 Stars Only</option>
        </select>
      </div>

      {loading ? <p>Loading props...</p> : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '3rem', opacity: 0.5 }}>
          <p>No player props available.</p>
          <p style={{ fontSize: '0.85rem' }}>Props are fetched daily from The Odds API. Run the pipeline to load today's props.</p>
        </div>
      ) : (
        Object.entries(grouped).map(([matchup, players]) => (
          <div key={matchup} style={{ marginBottom: '1.5rem' }}>
            <h3 style={{ fontSize: '1rem', color: '#94a3b8', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.3rem', marginBottom: '0.5rem' }}>
              {matchup}
            </h3>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.15)', textAlign: 'left' }}>
                  <th style={{ padding: '0.4rem 0.5rem', width: '25%' }}>Player</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Market</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Line</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Over</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Under</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Book</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Proj</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Edge%</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Conf</th>
                  <th style={{ padding: '0.4rem 0.5rem' }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(players).map(([player, playerProps]) => {
                  // Group by market for this player
                  const byMarket: Record<string, PropData[]> = {};
                  playerProps.forEach(p => {
                    if (!byMarket[p.market]) byMarket[p.market] = [];
                    byMarket[p.market].push(p);
                  });

                  return Object.entries(byMarket).map(([mkt, mktProps]) => {
                    const over = mktProps.find(p => p.outcome === 'Over');
                    const under = mktProps.find(p => p.outcome === 'Under');
                    const line = over?.line ?? under?.line;
                    const label = mktProps[0]?.market_label ?? mkt;

                    return (
                      <tr key={`${player}-${mkt}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        <td style={{ padding: '0.4rem 0.5rem', fontWeight: 500 }}>{player}</td>
                        <td style={{ padding: '0.4rem 0.5rem' }}>
                          <span style={{ background: 'rgba(255,255,255,0.08)', padding: '0.1rem 0.4rem', borderRadius: '3px', fontSize: '0.8rem' }}>
                            {label}
                          </span>
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace' }}>
                          {line != null ? line : '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace', color: over ? oddsColor(over.odds) : '#666' }}>
                          {over ? formatOdds(over.odds) : '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace', color: under ? oddsColor(under.odds) : '#666' }}>
                          {under ? formatOdds(under.odds) : '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontSize: '0.75rem', opacity: 0.5 }}>
                          {mktProps[0]?.bookmaker}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace' }}>
                          {over?.projection ?? under?.projection ?? '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontFamily: 'monospace', color: (over?.edge_pct ?? under?.edge_pct ?? 0) > 0 ? '#4ade80' : '#e2e8f0' }}>
                          {(over?.edge_pct ?? under?.edge_pct) != null ? `${(over?.edge_pct ?? under?.edge_pct)?.toFixed(1)}%` : '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem' }}>
                          {stars(over?.confidence ?? under?.confidence)}
                        </td>
                        <td style={{ padding: '0.4rem 0.5rem', fontSize: '0.75rem', opacity: 0.5 }}>
                          {over?.source ?? under?.source ?? '-'}
                          {(over?.is_stale || under?.is_stale) && <span style={{ color: '#fbbf24', marginLeft: '0.3rem' }}>stale</span>}
                        </td>
                      </tr>
                    );
                  });
                })}
              </tbody>
            </table>
          </div>
        ))
      )}
    </div>
  );
}
