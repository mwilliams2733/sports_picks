import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { useTodaysPicks } from '../hooks/useTodaysPicks';
import type { PropData, PickData } from '../types';
import SummaryBar from '../components/SummaryBar';
import GameCard from '../components/GameCard';
import PicksTable from '../components/PicksTable';
import ConfidenceStars from '../components/ConfidenceStars';
import BetModal from '../components/BetModal';
import CreditUsage from '../components/CreditUsage';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'boxing', 'mma'] as const;

export default function TodaysPicks() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { sport, setSport } = useAppStore();

  // Sync URL → store on mount
  useEffect(() => {
    const urlSport = searchParams.get('sport');
    if (urlSport && urlSport !== sport) setSport(urlSport);
  }, []);

  // Sync store → URL on sport change
  const handleSportChange = (s: string) => {
    setSport(s);
    setSearchParams(s === 'all' ? {} : { sport: s });
  };

  const { picks, record, games } = useTodaysPicks(sport);
  const [topProps, setTopProps] = useState<PropData[]>([]);
  const [betModalOpen, setBetModalOpen] = useState(false);
  const [betModalData, setBetModalData] = useState<{
    pickValue: string; pickType: string; odds: number; gameId: number;
    edgePct?: number; propMarket?: string; propPlayer?: string;
  } | null>(null);

  const handleBetPick = (pick: PickData) => {
    setBetModalData({
      pickValue: pick.pick_value,
      pickType: pick.pick_type,
      odds: pick.odds_at_pick,
      gameId: pick.game_id,
      edgePct: pick.edge_pct,
    });
    setBetModalOpen(true);
  };

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

  const handleBetFromCard = (bet: { pickValue: string; pickType: string; odds: number; gameId: number; edgePct?: number }) => {
    setBetModalData(bet);
    setBetModalOpen(true);
  };

  const sportParam = sport === 'all' ? undefined : sport;

  useEffect(() => {
    api.props.today(sportParam).then(allProps => {
      setTopProps(allProps.filter((p: PropData) => p.confidence !== null && p.confidence >= 3)
        .sort((a: PropData, b: PropData) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))
        .slice(0, 10));
    }).catch(() => {});
  }, [sportParam]);

  const error = picks.error || record.error || games.error;
  const loading = picks.isLoading || record.isLoading || games.isLoading;

  if (error) return <div className="empty-state"><div className="empty-state-title text-red">Error: {(error as Error).message}</div></div>;
  if (loading) return <div className="loading"><div className="spinner" /> Loading picks...</div>;

  const picksData = picks.data ?? [];
  const recordData = record.data ?? null;
  const gamesData = games.data ?? [];

  // Count games per sport from currently loaded games
  const sportCounts: Record<string, number> = {};
  gamesData.forEach(g => {
    sportCounts[g.sport] = (sportCounts[g.sport] || 0) + 1;
  });

  return (
    <div>
      <div className="toolbar">
        <div className="tab-group">
          {SPORTS.map(s => (
            <button key={s} className={`tab${sport === s ? ' active' : ''}`} onClick={() => handleSportChange(s)}>
              {s.toUpperCase()}
              {s !== 'all' && sportCounts[s] ? ` (${sportCounts[s]})` : ''}
              {s === 'all' && gamesData.length > 0 ? ` (${gamesData.length})` : ''}
            </button>
          ))}
        </div>
      </div>

      <SummaryBar record={recordData} pickCount={picksData.length} strategyName="Ensemble" />

      {gamesData.length > 0 && (
        <div style={{ marginTop: '1.25rem' }}>
          <div className="section-header">
            Today's Games <span className="section-divider" />
          </div>
          <div className="game-card-grid">
            {gamesData.map(game => (
              <GameCard
                key={game.id}
                game={game}
                picks={picksData}
                onBet={handleBetFromCard}
              />
            ))}
          </div>
        </div>
      )}

      {picksData.length > 0 && (
        <div style={{ marginTop: '1.25rem' }}>
          <div className="section-header">
            AI Picks <span className="section-divider" />
          </div>
          <PicksTable picks={picksData} onBet={handleBetPick} />
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
                  <th>Action</th>
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
                    <td>
                      <button className="btn-bet" onClick={() => handleBetProp(p)}>
                        Bet This
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {gamesData.length === 0 && picksData.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-title">No games or picks today</div>
          <div className="empty-state-sub">Run the pipeline to fetch today's games and odds.</div>
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

      <CreditUsage />
    </div>
  );
}
