import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, getErrorMessage, ApiError } from '../api/client';
import { useLeaderboard } from '../hooks/useLeaderboard';
import { useRankings } from '../hooks/useRankings';
import { usePaperTradingData, useUserDetail } from '../hooks/usePaperTrading';
import { useUserStore } from '../stores/userStore';
import { useFeedStore } from '../stores/feedStore';
import type { AvailableGameQuote, AvailablePropQuote, BetLeg, GamePickType, UserProfile } from '../types';
import { useToast } from '../hooks/useToast';
import { getPin, setPin } from '../lib/secrets';
import LeaderboardBar from '../components/LeaderboardBar';
import QuotePicker from '../components/QuotePicker';
import PropQuotePicker from '../components/PropQuotePicker';
import { useGameQuotes, usePropQuotes } from '../hooks/useQuotes';
import { addOrReplaceLeg, ageLabel, formatOdds, legFromQuote, parlayEstimate, priceMoveNote } from '../lib/quotes';

export default function PaperTrading() {
  const queryClient = useQueryClient();
  const { data: users = [], isLoading: usersLoading } = useLeaderboard();
  const { data: rankings = [] } = useRankings();
  const { selectedUser, setSelectedUser } = useUserStore();
  const { events: wsEvents } = useFeedStore();
  const { games: gamesQuery, feed: feedQuery } = usePaperTradingData();
  const { picks: userPicksQuery, stats: userStatsQuery } = useUserDetail(selectedUser?.id);
  const games = gamesQuery.data ?? [];
  const initialFeedEvents = feedQuery.data ?? [];
  const userPicks = userPicksQuery.data ?? [];
  const userStats = userStatsQuery.data ?? null;
  const [newName, setNewName] = useState('');
  const [newPin, setNewPin] = useState('');
  const [betPin, setBetPin] = useState('');
  const { toast } = useToast();

  // Prefills the (editable) PIN field when the selected user's id changes,
  // derived during render rather than in an effect so re-selecting the same
  // user (e.g. a new object identity after a leaderboard refetch) doesn't
  // clobber a PIN the player is mid-typing.
  const [pinFor, setPinFor] = useState<number | undefined>(selectedUser?.id);
  if (selectedUser?.id !== pinFor) {
    setPinFor(selectedUser?.id);
    setBetPin(selectedUser ? getPin(selectedUser.id) ?? '' : '');
  }

  // Place pick form state. The price is never typed: the player picks a side
  // the server has quoted, and the server prices it again on placement.
  const [selectedGame, setSelectedGame] = useState<number | null>(null);
  const [pickType, setPickType] = useState<GamePickType | 'prop'>('moneyline');
  const [chosen, setChosen] = useState<AvailableGameQuote | AvailablePropQuote | null>(null);
  const [stake, setStake] = useState(100);
  const gameQuotes = useGameQuotes(selectedGame);
  const propQuotes = usePropQuotes(pickType === 'prop' ? selectedGame : null);
  const game = games.find(g => g.id === selectedGame);
  const newestQuote = (gameQuotes.data?.quotes ?? [])
    .flatMap(q => (q.available ? [q.quoted_at] : []))
    .sort()
    .pop();

  // Parlay builder state
  type ParlayLeg = { leg: BetLeg; label: string; sport: string; odds: number };
  const [parlayLegs, setParlayLegs] = useState<ParlayLeg[]>([]);
  const [parlayStake, setParlayStake] = useState(100);

  // Selecting a user is now only that. Their picks and stats are queries
  // keyed on the id, so they load, cache and refresh themselves.
  const selectUser = (user: UserProfile) => {
    setSelectedUser(user);
  };

  const handleCreateUser = async () => {
    if (!newName.trim()) return;
    if (!/^\d{4,6}$/.test(newPin)) {
      toast('PIN must be 4–6 digits', 'error');
      return;
    }
    try {
      const res = await api.users.create(newName.trim(), newPin);
      setPin(res.id, newPin);
      setNewName('');
      setNewPin('');
      toast('User created!', 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
    } catch (e) {
      toast(getErrorMessage(e), 'error');
    }
  };

  const forgetPinOn401 = (e: unknown) => {
    if (e instanceof ApiError && e.status === 401 && selectedUser) {
      setPin(selectedUser.id, null);
      setBetPin('');
    }
  };

  const handlePlacePick = async () => {
    if (!selectedUser || selectedGame === null || !chosen) return;
    try {
      const res = await api.users.placePick(selectedUser.id,
        { ...legFromQuote(selectedGame, chosen), stake }, betPin);
      setPin(selectedUser.id, betPin);
      toast(`${priceMoveNote(chosen.pick_value, chosen.odds, res.pick_value, res.odds)}. `
        + `Balance: $${res.new_balance.toLocaleString()}`, 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setChosen(null);
    } catch (e) {
      forgetPinOn401(e);
      toast(getErrorMessage(e), 'error');
    }
  };

  const addParlayLeg = () => {
    if (selectedGame === null || !chosen) return;
    const matchup = game ? `${game.away_team}@${game.home_team} ` : '';
    const newLeg: ParlayLeg = {
      leg: legFromQuote(selectedGame, chosen),
      label: `${matchup}${chosen.pick_value}`,
      sport: game?.sport ?? '',
      odds: chosen.odds,
    };
    setParlayLegs(prev => addOrReplaceLeg(prev, newLeg));
    setChosen(null);
  };

  const removeParlayLeg = (index: number) => {
    setParlayLegs(prev => prev.filter((_, i) => i !== index));
  };

  const estimate = parlayEstimate(parlayLegs.map(l => l.odds));

  const handlePlaceParlay = async () => {
    if (!selectedUser || parlayLegs.length < 2) return;
    try {
      const res = await api.users.placeParlay(selectedUser.id, {
        legs: parlayLegs.map(l => l.leg), stake: parlayStake }, betPin);
      setPin(selectedUser.id, betPin);
      toast(`${parlayLegs.length}-leg parlay placed at ${formatOdds(res.combined_odds)}. `
        + `Potential: $${res.potential_payout.toLocaleString()}`, 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setParlayLegs([]);
    } catch (e) {
      forgetPinOn401(e);
      toast(getErrorMessage(e), 'error');
    }
  };

  const handleGameSelect = (gameId: number | null) => {
    setSelectedGame(gameId);
    setChosen(null);
  };

  const handlePickTypeChange = (type: GamePickType | 'prop') => {
    setPickType(type);
    setChosen(null);
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

  // Merge WS events + initial API events for the feed
  const allFeedEvents = [
    ...wsEvents.map(e => ({ message: e.message, timestamp: e.timestamp })),
    ...initialFeedEvents.filter(
      ie => !wsEvents.some(we => we.timestamp === ie.timestamp && we.message === ie.message)
    ),
  ];

  if (usersLoading) return <div className="loading"><div className="spinner" /> Loading...</div>;

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
        <input
          aria-label="PIN"
          className="input"
          type="password"
          inputMode="numeric"
          autoComplete="off"
          maxLength={6}
          placeholder={'PIN (4–6 digits)'}
          value={newPin}
          onChange={e => setNewPin(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreateUser()}
          style={{ maxWidth: '140px' }}
        />
        <button className="btn btn-primary" onClick={handleCreateUser}>Join</button>
      </div>

      {/* Compact Leaderboard Bar */}
      <div className="section-header">Leaderboard <span className="section-divider" /></div>
      <LeaderboardBar
        rows={rankings}
        selectedId={selectedUser?.id ?? null}
        onSelect={id => { const u = users.find(x => x.id === id); if (u) selectUser(u); }}
      />
      {users.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0.5rem' }}>
          No players yet. Join above!
        </div>
      )}

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

            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end', marginBottom: '0.75rem' }}>
              <div style={{ flex: 2, minWidth: '180px' }}>
                <div className="input-label">Game</div>
                <select className="input" value={selectedGame ?? ''}
                  onChange={e => handleGameSelect(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Select game...</option>
                  {games.map(g => (
                    <option key={g.id} value={g.id}>
                      {g.away_team} @ {g.home_team} ({g.sport.toUpperCase()})
                    </option>
                  ))}
                </select>
              </div>
              {newestQuote && <span className="quote-age">{ageLabel(newestQuote)}</span>}
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
              {(['moneyline', 'spread', 'over_under', 'prop'] as const).map(t => (
                <button
                  key={t}
                  className={`btn ${pickType === t ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => handlePickTypeChange(t)}
                  style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
                >
                  {t === 'over_under' ? 'Total' : t === 'prop' ? 'Prop' : t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>

            {selectedGame === null ? (
              <div className="text-muted">Choose a game to see its prices.</div>
            ) : pickType === 'prop' ? (
              <PropQuotePicker
                quotes={propQuotes.data?.quotes ?? []}
                selected={chosen && !('side' in chosen) ? chosen : null}
                onSelect={setChosen}
              />
            ) : (
              <QuotePicker
                quotes={gameQuotes.data?.quotes ?? []}
                pickType={pickType}
                homeName={game?.home_team ?? 'Home'}
                awayName={game?.away_team ?? 'Away'}
                selected={chosen && 'side' in chosen ? chosen.side : null}
                onSelect={setChosen}
              />
            )}

            {chosen && (
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end', marginTop: '0.75rem' }}>
                <div style={{ minWidth: '120px' }}>
                  <div className="input-label">Stake ($)</div>
                  <input className="input" type="number" value={stake}
                    onChange={e => setStake(Number(e.target.value))} min={1} />
                </div>
                <div style={{ minWidth: '120px' }}>
                  <div className="input-label">PIN</div>
                  <input
                    aria-label="PIN"
                    className="input"
                    type="password"
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={6}
                    placeholder={'PIN (4–6 digits)'}
                    value={betPin}
                    onChange={e => setBetPin(e.target.value)}
                  />
                </div>
                <button className="btn btn-primary" onClick={handlePlacePick}>
                  Place {chosen.pick_value} {formatOdds(chosen.odds)}
                </button>
              </div>
            )}
          </div>

          {/* Parlay Builder */}
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="input-label" style={{ marginBottom: '0.5rem' }}>
              Parlay Builder
              <span className="text-muted" style={{ fontWeight: 400, marginLeft: '0.5rem', fontSize: '0.75rem' }}>
                Combine picks across any sport
              </span>
            </div>

            {/* Add leg button in the existing pick form */}
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={addParlayLeg}
                disabled={!chosen}
                style={{ fontSize: '0.8rem' }}>
                + Add Leg
              </button>
              <span className="text-muted" style={{ alignSelf: 'center', fontSize: '0.8rem' }}>
                Pick a side above, then "Add Leg" to build your parlay
              </span>
            </div>

            {/* Parlay legs list */}
            {parlayLegs.length > 0 && (
              <div style={{ marginBottom: '0.75rem' }}>
                {parlayLegs.map((pl, i) => (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                    padding: '0.5rem 0.75rem', background: 'var(--card-hover)',
                    borderRadius: '0.375rem', marginBottom: '0.25rem', fontSize: '0.85rem',
                  }}>
                    <span className="badge badge-blue" style={{ fontSize: '0.65rem' }}>{pl.sport.toUpperCase()}</span>
                    <span className="font-medium" style={{ flex: 1 }}>{pl.label}</span>
                    <span className="mono">{formatOdds(pl.odds)}</span>
                    <button onClick={() => removeParlayLeg(i)}
                      style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', fontSize: '1rem' }}>
                      &#x2715;
                    </button>
                  </div>
                ))}

                {/* Parlay summary */}
                <div style={{
                  display: 'flex', flexWrap: 'wrap', gap: '1rem', alignItems: 'center', padding: '0.75rem',
                  background: 'var(--bg)', borderRadius: '0.375rem', marginTop: '0.5rem',
                  border: '1px solid var(--border)',
                }}>
                  <div>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>Legs</div>
                    <div className="font-medium">{parlayLegs.length}</div>
                  </div>
                  <div>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>Combined Odds</div>
                    <div className="mono font-medium" style={{ color: 'var(--green)' }}>
                      {estimate ? formatOdds(estimate.american) : '—'}
                    </div>
                    <div className="quote-reason">estimate — confirmed when placed</div>
                  </div>
                  <div>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>Potential Win</div>
                    <div className="mono font-medium" style={{ color: 'var(--green)' }}>
                      {estimate
                        ? `$${(parlayStake * (estimate.decimal - 1)).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                        : '—'}
                    </div>
                  </div>
                  <div style={{ minWidth: '100px' }}>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>Stake ($)</div>
                    <input className="input" type="number" value={parlayStake}
                      onChange={e => setParlayStake(Number(e.target.value))} min={1}
                      style={{ padding: '0.25rem 0.5rem', fontSize: '0.85rem' }} />
                  </div>
                  <div style={{ minWidth: '100px' }}>
                    <div className="text-muted" style={{ fontSize: '0.7rem' }}>PIN</div>
                    <input
                      aria-label="PIN"
                      className="input"
                      type="password"
                      inputMode="numeric"
                      autoComplete="off"
                      maxLength={6}
                      placeholder={'PIN (4–6 digits)'}
                      value={betPin}
                      onChange={e => setBetPin(e.target.value)}
                      style={{ padding: '0.25rem 0.5rem', fontSize: '0.85rem' }}
                    />
                  </div>
                  <button className="btn btn-success" onClick={handlePlaceParlay}
                    disabled={parlayLegs.length < 2}
                    style={{ alignSelf: 'end', whiteSpace: 'nowrap' }}>
                    Place Parlay
                  </button>
                </div>
              </div>
            )}

            {parlayLegs.length === 0 && (
              <div className="text-muted" style={{ fontSize: '0.8rem' }}>
                No legs added yet. Use the pick form above to select games or props, then click "Add Leg".
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
