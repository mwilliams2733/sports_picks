import { NavLink } from 'react-router-dom'

const TABS = [
  { path: '/', label: 'Lobby', icon: '🏟️' },
  // Phase 3 moves My Bets to /bets; until then it is the paper-trading page.
  { path: '/paper-trading', label: 'My Bets', icon: '🎟️' },
]

export default function MainTabs({ className, researchActive, onResearch }: {
  className: string; researchActive: boolean; onResearch: () => void
}) {
  return (
    <nav className={className} aria-label="Main navigation">
      {TABS.map(t => (
        <NavLink key={t.path} to={t.path} end={t.path === '/'}
          className={({ isActive }) => `sb-tab ${isActive ? 'active' : ''}`}>
          <span aria-hidden="true">{t.icon}</span><span>{t.label}</span>
        </NavLink>
      ))}
      <button type="button" className={`sb-tab ${researchActive ? 'active' : ''}`} onClick={onResearch}>
        <span aria-hidden="true">📚</span><span>Research</span>
      </button>
    </nav>
  )
}
