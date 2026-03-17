import { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { useLeaderboard } from '../hooks/useLeaderboard';
import { useUserStore } from '../stores/userStore';
import { useFeedStore } from '../stores/feedStore';
import type { PaperPickData, GameOddsData, UserStats, PropData, UserProfile } from '../types';
import { useToast } from '../components/Toast';

export default function PaperTrading() {
  const queryClient = useQueryClient();
  const { data: users = [], isLoading: usersLoading } = useLeaderboard();
  const { selectedUser, setSelectedUser } = useUserStore();
  const { events: wsEvents } = useFeedStore();
  const [userPicks, setUserPicks] = useState<PaperPickData[]>([]);
  const [userStats, setUserStats] = useState<UserStats | null>(null);
  const [games, setGames] = useState<GameOddsData[]>([]);
  const [newName, setNewName] = useState('');
  const { toast } = useToast();

  // Activity feed state (initial load from API + real-time from WS)
  const [initialFeedEvents, setInitialFeedEvents] = useState<{ message: string; timestamp: string }[]>([]);

  // Place pick form state
  const [selectedGame, setSelectedGame] = useState<number | ''>('');
  const [pickType, setPickType] = useState('moneyline');
  const [pickValue, setPickValue] = useState('');
  const [pickOdds, setPickOdds] = useState(-110);
  const [stake, setStake] = useState(10000);

  // Prop-specific state
  const [props, setProps] = useState<PropData[]>([]);
  const [selectedPropId, setSelectedPropId] = useState<number | ''>('');
  const [propSearch, setPropSearch] = useState('');

  const loadGames = async () => {
    const g = await api.games.today();
    setGames(g);
  };

  const loadProps = async () => {
    try {
      const p = await api.props.today();
      setProps(p);
    } catch {
      // Props may not be available
    }
  };

  const loadFeed = async () => {
    try {
      const feedData = await api.users.feed(20);
      setInitialFeedEvents(
        feedData.map((e) => ({
          message: e.payload?.message || `${e.event_type}`,
          timestamp: e.created_at,
        }))
      );
    } catch {
      // Feed may not be available
    }
  };

  useEffect(() => { loadGames(); loadProps(); loadFeed(); }, []);

  const selectUser = async (user: UserProfile) => {
    setSelectedUser(user);
    const [picks, stats] = await Promise.all([
      api.users.picks(user.id),
      api.users.stats(user.id),
    ]);
    setUserPicks(picks);
    setUserStats(stats);
  };

  const handleCreateUser = async () => {
    if (!newName.trim()) return;
    try {
      await api.users.create(newName.trim());
      setNewName('');
      toast('User created!', 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
    } catch (e: any) {
      toast(e.message, 'error');
    }
  };

  const handlePlacePick = async () => {
    if (!selectedUser) return;

    if (pickType === 'prop') {
      // Prop pick — use selected prop
      const prop = props.find(p => p.id === selectedPropId);
      if (!prop || !prop.line) return;
      try {
        const propPickValue = `${prop.player_name} ${prop.outcome} ${prop.line} ${prop.market_label}`;
        const result = await api.users.placePick(selectedUser.id, {
          game_id: prop.game_id,
          pick_type: 'prop',
          pick_value: propPickValue,
          odds: prop.odds,
          stake: stake,
          prop_market: prop.market,
          prop_player: prop.player_name,
        });
        const msg = result.result
          ? `Pick graded: ${result.result}! Balance: $${result.new_balance.toLocaleString()}`
          : `Pick placed! Balance: $${result.new_balance.toLocaleString()}`;
        toast(msg, result.result === 'loss' ? 'error' : 'success');
        queryClient.invalidateQueries({ queryKey: ['users'] });
        selectUser(selectedUser);
        setSelectedPropId('');
        setPropSearch('');
      } catch (e: any) {
        toast(e.message, 'error');
      }
    } else {
      // Game pick (moneyline, spread, over/under)
      if (!selectedGame || !pickValue.trim()) return;
      try {
        const result = await api.users.placePick(selectedUser.id, {
          game_id: selectedGame as number,
          pick_type: pickType,
          pick_value: pickValue,
          odds: pickOdds,
          stake: stake,
        });
        const msg = result.result
          ? `Pick graded: ${result.result}! Balance: $${result.new_balance.toLocaleString()}`
          : `Pick placed! Balance: $${result.new_balance.toLocaleString()}`;
        toast(msg, result.result === 'loss' ? 'error' : 'success');
        queryClient.invalidateQueries({ queryKey: ['users'] });
        selectUser(selectedUser);
        setPickValue('');
      } catch (e: any) {
        toast(e.message, 'error');
      }
    }
  };

  // Auto-fill odds when game + pick type changes
  const handleGameSelect = (gameId: number) => {
    setSelectedGame(gameId);
    const game = games.find(g => g.id === gameId);
    if (game) {
      if (pickType === 'moneyline') {
        setPickValue('HOME ML');
        setPickOdds(game.moneyline_home ?? -110);
      } else if (pickType === 'spread') {
        setPickValue(`HOME ${game.spread_home ?? -3.5}`);
        setPickOdds(-110);
      } else if (pickType === 'over_under') {
        setPickValue(`Over ${game.over_under ?? 220}`);
        setPickOdds(-110);
      }
    }
  };

  const handlePickTypeChange = (type: string) => {
    setPickType(type);
    if (type === 'prop') {
      setSelectedGame('');
      setPickValue('');
    } else if (selectedGame) {
      // Re-trigger auto-fill for game picks
      const game = games.find(g => g.id === selectedGame);
      if (game) {
        if (type === 'moneyline') {
          setPickValue('HOME ML');
          setPickOdds(game.moneyline_home ?? -110);
        } else if (type === 'spread') {
          setPickValue(`HOME ${game.spread_home ?? -3.5}`);
          setPickOdds(-110);
        } else if (type === 'over_under') {
          setPickValue(`Over ${game.over_under ?? 220}`);
          setPickOdds(-110);
        }
      }
    }
  };

  // Deduplicate props by player+market (group Over/Under into one entry)
  const uniqueProps = props.reduce<PropData[]>((acc, p) => {
    const key = `${p.player_name}-${p.market}`;
    if (!acc.find(x => `${x.player_name}-${x.market}` === key)) {
      acc.push(p);
    }
    return acc;
  }, []);

  // Filter props by search term
  const filteredProps = propSearch.trim()
    ? uniqueProps.filter(p =>
        p.player_name.toLowerCase().includes(propSearch.toLowerCase()) ||
        p.market_label.toLowerCase().includes(propSearch.toLowerCase()) ||
        p.matchup.toLowerCase().includes(propSearch.toLowerCase())
      )
    : uniqueProps;

  // Get Over/Under options for a selected player+market
  const getSelectedPropOptions = () => {
    if (!selectedPropId) return [];
    const selected = props.find(p => p.id === selectedPropId);
    if (!selected) return [];
    return props.filter(p =>
      p.player_name === selected.player_name && p.market === selected.market
    );
  };

  const formatMoney = (n: number) => {
    if (Math.abs(n) >= 1000000) return `$${(n / 1000000).toFixed(2)}M`;
    if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}K`;
    return `$${n.toFixed(0)}`;
  };

  const formatTime = (ts: string) => {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return ts;
    }
  };

  const getStreakIndicator = (user: UserProfile) => {
    if (user.current_streak >= 3 && user.streak_type === 'win') return '\u{1F525}';
    if (user.current_streak >= 3 && user.streak_type === 'loss') return '\u{2744}\u{FE0F}';
    return null;
  };

  // Merge WS events + initial API events for the feed
  const allFeedEvents = [
    ...wsEvents.map(e => ({ message: e.message, timestamp: e.timestamp })),
    ...initialFeedEvents.filter(
      ie => !wsEvents.some(we => we.timestamp === ie.timestamp && we.message === ie.message)
    ),
  ];

  if (usersLoading) return <div className="loading"><div className="spinner" /> Loading...</div>;

  const selectedProp = props.find(p => p.id === selectedPropId);
  const propOptions = getSelectedPropOptions();

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Paper Trading</h2>
      </div>

      {/* Create User */}
      <div className="card" style={{ marginBottom: '1.25rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="input"
          placeholder="Enter your name..."
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreateUser()}
          style={{ flex: 1, minWidth: '200px' }}
        />
        <button className="btn btn-primary" onClick={handleCreateUser}>Join</button>
      </div>

      {/* Compact Leaderboard Bar */}
      <div className="section-header">Leaderboard <span className="section-divider" /></div>
      <div className="leaderboard-bar">
        {users.map((u, i) => (
          <div
            key={u.id}
            className={`leaderboard-entry${selectedUser?.id === u.id ? ' selected' : ''}`}
            onClick={() => selectUser(u)}
          >
            <span className="leaderboard-rank">#{i + 1}</span>
            <span className="leaderboard-name">{u.name}</span>
            <span className="leaderboard-roi" style={{ color: u.roi >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {u.roi >= 0 ? '+' : ''}{u.roi}%
            </span>
            <span className="leaderboard-roi">{formatMoney(u.current_balance)}</span>
            {getStreakIndicator(u) && (
              <span className="leaderboard-streak">{getStreakIndicator(u)}</span>
            )}
          </div>
        ))}
        {users.length === 0 && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0.5rem' }}>
            No users yet. Create one above!
          </div>
        )}
      </div>

      {/* Activity Feed */}
      <div className="activity-feed">
        <div className="activity-feed-header">Activity Feed</div>
        {allFeedEvents.length > 0 ? (
          allFeedEvents.map((event, i) => (
            <div key={`${event.timestamp}-${i}`} className="feed-event">
              <span className="feed-event-message">{event.message}</span>
              <span className="feed-event-time">{formatTime(event.timestamp)}</span>
            </div>
          ))
        ) : (
          <div className="feed-empty">No activity yet. Place some picks!</div>
        )}
      </div>

      {/* Selected User Detail */}
      {selectedUser && (
        <div style={{ marginTop: '1.25rem' }}>
          <div className="section-header">{selectedUser.name} <span className="section-divider" /></div>

          {/* Stats Cards */}
          <div className="results-grid" style={{ marginBottom: '1rem' }}>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">ROI</div>
              <div className="stat-value" style={{ fontSize: '1.25rem', color: selectedUser.roi >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {selectedUser.roi >= 0 ? '+' : ''}{selectedUser.roi}%
              </div>
            </div>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">Record</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                {selectedUser.wins}-{selectedUser.losses}
              </div>
            </div>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">Best Streak</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                {selectedUser.best_streak}
              </div>
            </div>
          </div>

          {/* Period Stats */}
          {userStats && (
            <div className="table-wrap" style={{ marginBottom: '1rem' }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Period</th>
                    <th>Record</th>
                    <th>Win %</th>
                    <th>Profit</th>
                    <th>ROI</th>
                  </tr>
                </thead>
                <tbody>
                  {([
                    ['Today', userStats.today],
                    ['This Week', userStats.this_week],
                    ['This Month', userStats.this_month],
                    ['All Time', userStats.all_time],
                  ] as const).map(([label, s]) => (
                    <tr key={label}>
                      <td className="font-medium">{label}</td>
                      <td className="mono">{s.wins}-{s.losses}{s.pushes > 0 ? `-${s.pushes}` : ''}</td>
                      <td className="mono" style={{ color: s.win_rate >= 55 ? 'var(--green)' : s.win_rate > 0 ? undefined : 'var(--text-muted)' }}>
                        {s.total > 0 ? `${s.win_rate}%` : '\u2014'}
                      </td>
                      <td className="mono" style={{ color: s.profit >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {s.total > 0 ? `${s.profit >= 0 ? '+' : ''}${formatMoney(s.profit)}` : '\u2014'}
                      </td>
                      <td className="mono" style={{ color: s.roi >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {s.total > 0 ? `${s.roi >= 0 ? '+' : ''}${s.roi}%` : '\u2014'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Place Pick Form */}
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="input-label" style={{ marginBottom: '0.5rem' }}>Place a Pick</div>

            {/* Pick Type Selector */}
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
              {(['moneyline', 'spread', 'over_under', 'prop'] as const).map(t => (
                <button
                  key={t}
                  className={`btn ${pickType === t ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => handlePickTypeChange(t)}
                  style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
                >
                  {t === 'over_under' ? 'Over/Under' : t === 'prop' ? 'Player Prop' : t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>

            {pickType === 'prop' ? (
              /* Prop Pick Form */
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end' }}>
                <div style={{ flex: 3, minWidth: '250px' }}>
                  <div className="input-label">Search Player Props</div>
                  <input
                    className="input"
                    value={propSearch}
                    onChange={e => { setPropSearch(e.target.value); setSelectedPropId(''); }}
                    placeholder="Search by player, market, or matchup..."
                  />
                  {propSearch.trim() && filteredProps.length > 0 && !selectedPropId && (
                    <div style={{
                      border: '1px solid var(--border)',
                      borderRadius: '0.375rem',
                      maxHeight: '200px',
                      overflowY: 'auto',
                      marginTop: '0.25rem',
                      background: 'var(--card-bg)',
                    }}>
                      {filteredProps.slice(0, 20).map(p => (
                        <div
                          key={`${p.player_name}-${p.market}-${p.outcome}`}
                          onClick={() => {
                            setSelectedPropId(p.id);
                            setPropSearch(`${p.player_name} - ${p.market_label}`);
                          }}
                          style={{
                            padding: '0.5rem 0.75rem',
                            cursor: 'pointer',
                            borderBottom: '1px solid var(--border)',
                            fontSize: '0.85rem',
                          }}
                          onMouseEnter={e => (e.currentTarget.style.background = 'var(--card-hover)')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >
                          <span className="font-medium">{p.player_name}</span>
                          <span className="text-muted"> - {p.market_label}</span>
                          <span className="text-muted" style={{ fontSize: '0.75rem' }}> ({p.matchup})</span>
                          {p.line != null && (
                            <span className="mono" style={{ marginLeft: '0.5rem' }}>
                              {p.outcome} {p.line} ({p.odds > 0 ? '+' : ''}{p.odds})
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Show Over/Under toggle when a prop is selected */}
                {selectedProp && propOptions.length > 1 && (
                  <div style={{ minWidth: '140px' }}>
                    <div className="input-label">Side</div>
                    <select
                      className="input"
                      value={selectedPropId}
                      onChange={e => setSelectedPropId(Number(e.target.value))}
                    >
                      {propOptions.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.outcome} {p.line} ({p.odds > 0 ? '+' : ''}{p.odds})
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {selectedProp && (
                  <div style={{ minWidth: '120px' }}>
                    <div className="input-label">Stake ($)</div>
                    <input className="input" type="number" value={stake} onChange={e => setStake(Number(e.target.value))} min={1} />
                  </div>
                )}

                {selectedProp && (
                  <button className="btn btn-primary" onClick={handlePlacePick} style={{ alignSelf: 'end' }}>
                    Place Prop
                  </button>
                )}
              </div>
            ) : (
              /* Game Pick Form */
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end' }}>
                <div style={{ flex: 2, minWidth: '180px' }}>
                  <div className="input-label">Game</div>
                  <select className="input" value={selectedGame} onChange={e => handleGameSelect(Number(e.target.value))}>
                    <option value="">Select game...</option>
                    {games.map(g => (
                      <option key={g.id} value={g.id}>
                        {g.away_team} @ {g.home_team} ({g.sport.toUpperCase()})
                      </option>
                    ))}
                  </select>
                </div>
                <div style={{ flex: 1, minWidth: '140px' }}>
                  <div className="input-label">Pick</div>
                  <input className="input" value={pickValue} onChange={e => setPickValue(e.target.value)} placeholder="HOME ML" />
                </div>
                <div style={{ minWidth: '90px' }}>
                  <div className="input-label">Odds</div>
                  <input className="input" type="number" value={pickOdds} onChange={e => setPickOdds(Number(e.target.value))} />
                </div>
                <div style={{ minWidth: '120px' }}>
                  <div className="input-label">Stake ($)</div>
                  <input className="input" type="number" value={stake} onChange={e => setStake(Number(e.target.value))} min={1} />
                </div>
                <button className="btn btn-primary" onClick={handlePlacePick} style={{ alignSelf: 'end' }}>
                  Place Pick
                </button>
              </div>
            )}

            {/* Selected prop summary */}
            {pickType === 'prop' && selectedProp && (
              <div style={{ marginTop: '0.75rem', padding: '0.5rem 0.75rem', background: 'var(--card-hover)', borderRadius: '0.375rem', fontSize: '0.85rem' }}>
                <span className="font-medium">{selectedProp.player_name}</span>
                {' '}<span className="badge badge-default">{selectedProp.market_label}</span>
                {' '}<span className="mono">{selectedProp.outcome} {selectedProp.line}</span>
                {' '}<span className="mono" style={{ color: selectedProp.odds > 0 ? 'var(--green)' : undefined }}>
                  ({selectedProp.odds > 0 ? '+' : ''}{selectedProp.odds})
                </span>
                {' '}<span className="text-muted">| {selectedProp.matchup}</span>
                {selectedProp.projection != null && (
                  <span className="text-muted"> | Proj: {selectedProp.projection}</span>
                )}
                {selectedProp.edge_pct != null && (
                  <span style={{ color: selectedProp.edge_pct > 0 ? 'var(--green)' : 'var(--red)' }}>
                    {' '}| Edge: {selectedProp.edge_pct > 0 ? '+' : ''}{selectedProp.edge_pct.toFixed(1)}%
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Pick History */}
          {userPicks.length > 0 && (
            <>
              <div className="input-label" style={{ marginBottom: '0.5rem' }}>Pick History</div>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Sport</th>
                      <th>Type</th>
                      <th>Pick</th>
                      <th>Odds</th>
                      <th>Stake</th>
                      <th>Result</th>
                      <th>Payout</th>
                    </tr>
                  </thead>
                  <tbody>
                    {userPicks.map(p => (
                      <tr key={p.id}>
                        <td className="mono text-muted">{p.date}</td>
                        <td>{p.sport.toUpperCase()}</td>
                        <td>
                          <span className={`badge ${p.pick_type === 'prop' ? 'badge-blue' : 'badge-default'}`}>
                            {p.pick_type === 'over_under' ? 'O/U' : p.pick_type === 'prop' ? 'Prop' : p.pick_type.charAt(0).toUpperCase() + p.pick_type.slice(1)}
                          </span>
                        </td>
                        <td className="font-medium">{p.pick_value}</td>
                        <td className="mono">{p.odds > 0 ? '+' : ''}{p.odds}</td>
                        <td className="mono">{formatMoney(p.stake)}</td>
                        <td>
                          {p.result === 'win' && <span className="badge badge-green">Win</span>}
                          {p.result === 'loss' && <span className="badge badge-red">Loss</span>}
                          {p.result === 'push' && <span className="badge badge-yellow">Push</span>}
                          {!p.result && <span className="badge badge-default">Pending</span>}
                        </td>
                        <td className="mono" style={{ color: (p.payout ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {p.payout != null ? `${p.payout >= 0 ? '+' : ''}${formatMoney(p.payout)}` : '\u2014'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
