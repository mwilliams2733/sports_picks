import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { PickData, RecordData, PropData, GameOddsData } from '../types';
import SummaryCards from '../components/SummaryCards';
import PicksTable from '../components/PicksTable';
import ConfidenceStars from '../components/ConfidenceStars';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'boxing', 'mma'] as const;

function formatOdds(odds: number | null): string {
  if (odds == null) return '-';
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatSpread(spread: number | null): string {
  if (spread == null) return '-';
  return spread > 0 ? `+${spread}` : `${spread}`;
}

export default function TodaysPicks() {
  const [picks, setPicks] = useState<PickData[]>([]);
  const [record, setRecord] = useState<RecordData | null>(null);
  const [games, setGames] = useState<GameOddsData[]>([]);
  const [topProps, setTopProps] = useState<PropData[]>([]);
  const [sport, setSport] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const sportParam = sport === 'all' ? undefined : sport;
    Promise.all([
      api.picks.today(sportParam),
      api.stats.record(sportParam),
      api.games.today(sportParam),
    ])
      .then(([p, r, g]) => { setPicks(p); setRecord(r); setGames(g); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));

    api.props.today(sportParam).then(allProps => {
      setTopProps(allProps.filter((p: PropData) => p.confidence !== null && p.confidence >= 3)
        .sort((a: PropData, b: PropData) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))
        .slice(0, 10));
    }).catch(() => {});
  }, [sport]);

  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {error}</div></div>;
  if (loading) return <div className="loading"><div className="spinner" /> Loading picks...</div>;

  return (
    <div>
      <div className="toolbar">
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => setSport(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <SummaryCards record={record} pickCount={picks.length} strategyName="Ensemble" />

      {picks.length > 0 && <PicksTable picks={picks} />}

      {games.length > 0 && (
        <div style={{ marginTop: '1.25rem' }}>
          <div className="section-header">
            Today's Games & Odds <span className="section-divider" />
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Sport</th>
                  <th>Matchup</th>
                  <th>Status</th>
                  <th>Spread</th>
                  <th>ML Home</th>
                  <th>ML Away</th>
                  <th>O/U</th>
                  <th>Book</th>
                </tr>
              </thead>
              <tbody>
                {games.map(g => (
                  <tr key={g.id}>
                    <td><span className="badge badge-default">{g.sport.toUpperCase()}</span></td>
                    <td className="font-medium">
                      {g.away_team} <span className="text-muted">@</span> {g.home_team}
                      {g.status === 'final' && (
                        <span className="text-muted mono" style={{ marginLeft: '0.5rem' }}>
                          ({g.away_score} - {g.home_score})
                        </span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${g.status === 'final' ? 'badge-green' : g.status === 'in_progress' ? 'badge-yellow' : 'badge-default'}`}>
                        {g.status}
                      </span>
                    </td>
                    <td className="mono">{formatSpread(g.spread_home)}</td>
                    <td className="mono" style={{ color: g.moneyline_home && g.moneyline_home > 0 ? 'var(--green)' : undefined }}>
                      {formatOdds(g.moneyline_home)}
                    </td>
                    <td className="mono" style={{ color: g.moneyline_away && g.moneyline_away > 0 ? 'var(--green)' : undefined }}>
                      {formatOdds(g.moneyline_away)}
                    </td>
                    <td className="mono">{g.over_under ?? '-'}</td>
                    <td className="text-muted" style={{ fontSize: '0.75rem' }}>{g.bookmaker ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {topProps.length > 0 && (
        <div style={{ marginTop: '1.5rem' }}>
          <div className="section-header">
            Top Props <span className="section-divider" />
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Market</th>
                  <th>Pick</th>
                  <th>Line</th>
                  <th>Projection</th>
                  <th>Edge</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {topProps.map(p => (
                  <tr key={p.id}>
                    <td className="font-medium text-primary">{p.player_name}</td>
                    <td><span className="badge badge-default">{p.market_label}</span></td>
                    <td className="text-green font-bold">{p.outcome}</td>
                    <td className="mono">{p.line}</td>
                    <td className="mono">{p.projection}</td>
                    <td className="mono text-green">{p.edge_pct?.toFixed(1)}%</td>
                    <td><ConfidenceStars rating={p.confidence ?? 0} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {games.length === 0 && picks.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-title">No games or picks today</div>
          <div className="empty-state-sub">Run the pipeline to fetch today's games and odds.</div>
        </div>
      )}
    </div>
  );
}
