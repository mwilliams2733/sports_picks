import { useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { useProps } from '../hooks/useProps';
import type { PropData } from '../types';
import ConfidenceStars from '../components/ConfidenceStars';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'boxing', 'mma'] as const;

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

export default function PlayerProps() {
  const { sport, setSport } = useAppStore();
  const [market, setMarket] = useState<string>('');
  const [minConfidence, setMinConfidence] = useState(0);

  const marketParam = market || undefined;
  const { props, markets } = useProps(sport, marketParam);

  const propsData = props.data ?? [];
  const marketsData = markets.data ?? [];

  const filtered = propsData
    .filter(p => p.confidence === null || p.confidence >= minConfidence)
    .sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0));

  const grouped = filtered.reduce<Record<string, Record<string, PropData[]>>>((acc, p) => {
    const key = p.matchup;
    if (!acc[key]) acc[key] = {};
    if (!acc[key][p.player_name]) acc[key][p.player_name] = [];
    acc[key][p.player_name].push(p);
    return acc;
  }, {});

  const error = props.error || markets.error;
  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {(error as Error).message}</div></div>;

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Player Props</h2>
      </div>

      <div className="toolbar">
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => setSport(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <select className="select" value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All Markets</option>
          {(marketsData as any[]).map((m: any) => {
            const key = typeof m === 'string' ? m : m.key;
            const label = typeof m === 'string' ? m : m.label;
            return <option key={key} value={key}>{label}</option>;
          })}
        </select>
        <select className="select" value={minConfidence} onChange={e => setMinConfidence(Number(e.target.value))}>
          <option value={0}>All Confidence</option>
          <option value={1}>1+ Stars</option>
          <option value={2}>2+ Stars</option>
          <option value={3}>3+ Stars</option>
          <option value={4}>4+ Stars</option>
          <option value={5}>5 Stars Only</option>
        </select>
      </div>

      {props.isLoading ? (
        <div className="loading"><div className="spinner" /> Loading props...</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No player props available</div>
          <div className="empty-state-sub">Props are fetched daily from The Odds API. Run the pipeline to load today's props.</div>
        </div>
      ) : (
        Object.entries(grouped).map(([matchup, players]) => (
          <div key={matchup} style={{ marginBottom: '1.25rem' }}>
            <div className="table-wrap">
              <div className="matchup-header">{matchup}</div>
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: '22%' }}>Player</th>
                    <th>Market</th>
                    <th>Line</th>
                    <th>Over</th>
                    <th>Under</th>
                    <th>Book</th>
                    <th>Proj</th>
                    <th>Edge</th>
                    <th>Conf</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(players).map(([player, playerProps]) => {
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
                      const edgePct = over?.edge_pct ?? under?.edge_pct;

                      return (
                        <tr key={`${player}-${mkt}`}>
                          <td className="font-medium text-primary">{player}</td>
                          <td><span className="badge badge-default">{label}</span></td>
                          <td className="mono">{line != null ? line : '-'}</td>
                          <td className="mono" style={{ color: over && over.odds > 0 ? 'var(--green)' : undefined }}>
                            {over ? formatOdds(over.odds) : '-'}
                          </td>
                          <td className="mono" style={{ color: under && under.odds > 0 ? 'var(--green)' : undefined }}>
                            {under ? formatOdds(under.odds) : '-'}
                          </td>
                          <td className="text-muted" style={{ fontSize: '0.75rem' }}>{mktProps[0]?.bookmaker}</td>
                          <td className="mono">{over?.projection ?? under?.projection ?? '-'}</td>
                          <td className="mono" style={{ color: edgePct && edgePct > 0 ? 'var(--green)' : undefined }}>
                            {edgePct != null ? `${edgePct.toFixed(1)}%` : '-'}
                          </td>
                          <td><ConfidenceStars rating={over?.confidence ?? under?.confidence ?? 0} /></td>
                          <td className="text-muted" style={{ fontSize: '0.75rem' }}>
                            {over?.source ?? under?.source ?? '-'}
                            {(over?.is_stale || under?.is_stale) && (
                              <span className="badge badge-yellow" style={{ marginLeft: '0.35rem' }}>stale</span>
                            )}
                          </td>
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
