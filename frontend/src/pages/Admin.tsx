import { useState, useEffect } from 'react';
import { api, getErrorMessage } from '../api/client';
import type { UserProfile } from '../types';
import { useToast } from '../hooks/useToast';

export default function Admin() {
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const { toast } = useToast();

  const appUrl = window.location.origin;

  const loadUsers = () => {
    api.users.list().then(setUsers).finally(() => setLoading(false));
  };

  useEffect(loadUsers, []);

  const handleDelete = async (userId: number, userName: string) => {
    try {
      await api.users.delete(userId);
      setUsers(prev => prev.filter(u => u.id !== userId));
      setConfirmDelete(null);
      toast(`Removed ${userName}`, 'success');
    } catch (e) {
      toast(`Error: ${getErrorMessage(e)}`, 'error');
    }
  };

  const inviteMessage = `Join our Sports Picks group! We use AI-powered analysis to find high-value bets across NBA, NFL, NCAAB, and more.\n\nSign up here: ${appUrl}/paper-trading`;

  const emailSubject = 'Join Sports Picks';
  const emailBody = `Hey!\n\n${inviteMessage}\n\nSee you on the leaderboard!`;

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      toast(`Copied ${label}!`, 'success');
      setTimeout(() => setCopied(null), 2000);
    } catch {
      toast('Failed to copy', 'error');
    }
  };

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Admin</h2>
      </div>

      {/* Invite Friends */}
      <div className="section-header">Invite Friends <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.5rem', padding: '1.25rem' }}>
        <div style={{ marginBottom: '1rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Share the invite link with your friends via text, email, or any messaging app.
        </div>
        <div style={{
          background: 'var(--bg)', border: '1px solid var(--border)',
          borderRadius: '0.5rem', padding: '0.75rem', marginBottom: '1rem',
          fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--text-muted)',
          whiteSpace: 'pre-wrap', lineHeight: 1.6,
        }}>
          {inviteMessage}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <button
            className="btn btn-primary"
            onClick={() => copyToClipboard(`${appUrl}/paper-trading`, 'link')}
          >
            {copied === 'link' ? 'Copied!' : 'Copy Link'}
          </button>
          <button
            className="btn btn-success"
            onClick={() => copyToClipboard(inviteMessage, 'iMessage')}
          >
            {copied === 'iMessage' ? 'Copied!' : 'Copy for iMessage'}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => copyToClipboard(`Subject: ${emailSubject}\n\n${emailBody}`, 'email')}
          >
            {copied === 'email' ? 'Copied!' : 'Copy for Email'}
          </button>
        </div>
      </div>

      {/* User Roster */}
      <div className="section-header">
        User Roster
        <span className="badge badge-default" style={{ marginLeft: '0.5rem' }}>{users.length} members</span>
        <span className="section-divider" />
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" /> Loading users...</div>
      ) : users.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No users yet</div>
          <div className="empty-state-sub">Share your invite link to get friends to join!</div>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Joined</th>
                <th>Balance</th>
                <th>Record</th>
                <th>Win %</th>
                <th>ROI</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td className="font-medium">{u.name}</td>
                  <td className="text-muted" style={{ fontSize: '0.8rem' }}>
                    {u.created_at ? new Date(u.created_at).toLocaleDateString() : '--'}
                  </td>
                  <td className="mono" style={{
                    color: u.current_balance > u.starting_balance ? 'var(--green)'
                      : u.current_balance < u.starting_balance ? 'var(--red)' : undefined
                  }}>
                    ${u.current_balance.toLocaleString()}
                  </td>
                  <td className="mono">{u.wins}-{u.losses}{u.pushes > 0 ? `-${u.pushes}` : ''}</td>
                  <td className="mono" style={{ color: u.win_rate >= 55 ? 'var(--green)' : undefined }}>
                    {u.wins + u.losses > 0 ? `${u.win_rate}%` : '--'}
                  </td>
                  <td className="mono" style={{
                    color: u.roi > 0 ? 'var(--green)' : u.roi < 0 ? 'var(--red)' : undefined
                  }}>
                    {u.total_wagered > 0 ? `${u.roi > 0 ? '+' : ''}${u.roi}%` : '--'}
                  </td>
                  <td>
                    {confirmDelete === u.id ? (
                      <div style={{ display: 'flex', gap: '0.25rem' }}>
                        <button className="btn-bet" style={{ background: 'var(--red)', fontSize: '0.7rem' }}
                          onClick={() => handleDelete(u.id, u.name)}>
                          Confirm
                        </button>
                        <button className="btn-bet" style={{ fontSize: '0.7rem' }}
                          onClick={() => setConfirmDelete(null)}>
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button className="btn-bet" style={{ fontSize: '0.75rem' }}
                        onClick={() => setConfirmDelete(u.id)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
