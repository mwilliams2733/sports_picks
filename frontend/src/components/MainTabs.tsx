import { NavLink } from 'react-router-dom'

const TABS = [
  { path: '/', label: 'Lobby', icon: '🏟️' },
  { path: '/bets', label: 'My Bets', icon: '🎟️' },
  { path: '/leaders', label: 'Leaders', icon: '🏆' },
]

export default function MainTabs({ className, researchActive, onResearch, openCount = 0 }: {
  className: string; researchActive: boolean; onResearch: () => void; openCount?: number
}) {
  return (
    <nav className={className} aria-label="Main navigation">
      {TABS.map(t => (
        <NavLink key={t.path} to={t.path} end={t.path === '/'}
          className={({ isActive }) => `sb-tab ${isActive ? 'active' : ''}`}>
          <span aria-hidden="true">{t.icon}</span><span>{t.label}</span>
          {t.path === '/bets' && openCount > 0 && (
            <span className="sb-badge" aria-label={`${openCount} open`}>{openCount}</span>
          )}
        </NavLink>
      ))}
      <button type="button" className={`sb-tab ${researchActive ? 'active' : ''}`} onClick={onResearch}>
        <span aria-hidden="true">📚</span><span>Research</span>
      </button>
    </nav>
  )
}
