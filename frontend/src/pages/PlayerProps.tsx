import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useProps } from '../hooks/useProps';
import type { PropData } from '../types';
import ConfidenceStars from '../components/ConfidenceStars';
import BetModal from '../components/BetModal';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'boxing', 'mma'] as const;

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

export default function PlayerProps() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { sport, setSport } = useAppStore();
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'edge' | 'confidence' | 'name'>('edge');
  const [minConfidence, setMinConfidence] = useState(0);

  useEffect(() => {
    const urlSport = searchParams.get('sport');
    const urlConf = searchParams.get('confidence');
    if (urlSport && urlSport !== sport) setSport(urlSport);
    if (urlConf) setMinConfidence(Number(urlConf));
  }, []);

  const handleSportChange = (s: string) => {
    setSport(s);
    const params: Record<string, string> = {};
    if (s !== 'all') params.sport = s;
    if (minConfidence > 0) params.confidence = String(minConfidence);
    setSearchParams(params);
  };
  const [betModalOpen, setBetModalOpen] = useState(false);
  const [betModalData, setBetModalData] = useState<{
    pickValue: string; pickType: string; odds: number; gameId: number;
    edgePct?: number; propMarket?: string; propPlayer?: string;
  } | null>(null);

  const handleBetProp = (p: PropData) => {
    setBetModalData({
      pickValue: `${p.player_name} ${p.outcome} ${p.line}`,
      pickType: 'prop',
      odds: p.odds,
      gameId: p.game_id,
      edgePct: p.edge_pct ?? undefined,
      propMarket: p.market,
      propPlayer: p.player_name,
    });
    setBetModalOpen(true);
  };

  const { props } = useProps(sport);

  const propsData = props.data ?? [];

  const filteredProps = propsData
    .filter(p => p.confidence === null || p.confidence >= minConfidence)
    .filter(p => !search || p.player_name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sortBy === 'edge') return (b.edge_pct ?? 0) - (a.edge_pct ?? 0);
      if (sortBy === 'confidence') return (b.confidence ?? 0) - (a.confidence ?? 0);
      return a.player_name.localeCompare(b.player_name);
    });

  const error = props.error;
  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {(error as Error).message}</div></div>;

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Player Props</h2>
      </div>

      <div className="toolbar">
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => handleSportChange(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="props-toolbar">
        <input
          className="input props-search"
          placeholder="Search player..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="select" value={sortBy} onChange={e => setSortBy(e.target.value as any)}>
          <option value="edge">Sort: Edge %</option>
          <option value="confidence">Sort: Confidence</option>
          <option value="name">Sort: Player Name</option>
        </select>
        <select className="select" value={minConfidence} onChange={e => setMinConfidence(Number(e.target.value))}>
          {[0, 1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}+ Stars</option>)}
        </select>
      </div>

      {props.isLoading ? (
        <div className="loading"><div className="spinner" /> Loading props...</div>
      ) : filteredProps.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No player props available</div>
          <div className="empty-state-sub">Props are fetched daily from The Odds API. Run the pipeline to load today's props.</div>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Market</th>
                <th>Line</th>
                <th>Projection</th>
                <th>Edge</th>
                <th>Confidence</th>
                <th>Odds</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredProps.map(prop => (
                <tr key={prop.game_id + prop.player_name + prop.market + prop.outcome}>
                  <td>
                    <div className="font-medium text-primary">{prop.player_name}</div>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>{prop.matchup}</div>
                    {prop.is_stale && (
                      <span className="badge badge-yellow" style={{ fontSize: '0.65rem', marginTop: '0.15rem' }}>
                        stale — {prop.source ?? 'unknown source'}
                      </span>
                    )}
                  </td>
                  <td><span className="badge badge-purple">{prop.market.replace('player_', '').replace(/_/g, ' ')}</span></td>
                  <td className="mono">{prop.outcome} {prop.line ?? '—'}</td>
                  <td className="mono">{prop.projection?.toFixed(1) || '—'}</td>
                  <td style={{ color: prop.edge_pct && prop.edge_pct > 0 ? 'var(--green)' : undefined, fontFamily: 'var(--font-mono)' }}>
                    {prop.edge_pct != null ? `${prop.edge_pct > 0 ? '+' : ''}${prop.edge_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td><ConfidenceStars rating={prop.confidence ?? 0} /></td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{formatOdds(prop.odds)}</td>
                  <td>
                    <button className="btn-bet" onClick={() => handleBetProp(prop)}>Bet This</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {betModalData && (
        <BetModal
          open={betModalOpen}
          onClose={() => setBetModalOpen(false)}
          pickValue={betModalData.pickValue}
          pickType={betModalData.pickType}
          odds={betModalData.odds}
          gameId={betModalData.gameId}
          edgePct={betModalData.edgePct}
          propMarket={betModalData.propMarket}
          propPlayer={betModalData.propPlayer}
        />
      )}
    </div>
  );
}
