import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, getErrorMessage } from '../api/client';
import { useLeaderboard } from '../hooks/useLeaderboard';
import { useRankings } from '../hooks/useRankings';
import { usePaperTradingData, useUserDetail } from '../hooks/usePaperTrading';
import { useUserStore } from '../stores/userStore';
import { useFeedStore } from '../stores/feedStore';
import type { UserProfile } from '../types';
import { useToast } from '../hooks/useToast';
import { setPin } from '../lib/secrets';
import LeaderboardBar from '../components/LeaderboardBar';

export default function PaperTrading() {
  const queryClient = useQueryClient();
  const { data: users = [], isLoading: usersLoading } = useLeaderboard();
  const { data: rankings = [] } = useRankings();
  const { selectedUser, setSelectedUser } = useUserStore();
  const { events: wsEvents } = useFeedStore();
  const { feed: feedQuery } = usePaperTradingData();
  const { picks: userPicksQuery, stats: userStatsQuery } = useUserDetail(selectedUser?.id);
  const initialFeedEvents = feedQuery.data ?? [];
  const userPicks = userPicksQuery.data ?? [];
  const userStats = userStatsQuery.data ?? null;
  const [newName, setNewName] = useState('');
  const [newPin, setNewPin] = useState('');
  const { toast } = useToast();

  // Selecting a user is now only that. Their picks and stats are queries
  // keyed on the id, so they load, cache and refresh themselves.
  const selectUser = (user: UserProfile) => {
    setSelectedUser(user);
  };
  // `selectedUser` is the row as it was when clicked; the users query is
  // what a placed bet invalidates, so money is read from there.
  const liveUser = users.find(u => u.id === selectedUser?.id) ?? selectedUser;

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
      {liveUser && (
        <div style={{ marginTop: '1.25rem' }}>
          <div className="section-header">{liveUser.name} <span className="section-divider" /></div>

          {/* Stats Cards */}
          <div className="results-grid" style={{ marginBottom: '1rem' }}>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">Available</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                {formatMoney(liveUser.available_balance)}
              </div>
            </div>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">ROI</div>
              <div className="stat-value" style={{ fontSize: '1.25rem', color: liveUser.roi >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {liveUser.roi >= 0 ? '+' : ''}{liveUser.roi}%
              </div>
            </div>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">Record</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                {liveUser.wins}-{liveUser.losses}
              </div>
            </div>
            <div className="stat-card" style={{ padding: '0.75rem' }}>
              <div className="stat-label">Best Streak</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                {liveUser.best_streak}
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

          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="input-label">Place bets from the Lobby</div>
            <p className="text-muted" style={{ margin: '0.35rem 0 0' }}>
              Tap any price in the <Link to="/">Lobby</Link> to add it to your bet slip — singles and parlays
              are both placed from there.
            </p>
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
