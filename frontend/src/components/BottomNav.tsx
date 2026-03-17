import { NavLink } from 'react-router-dom'

const TABS = [
  { path: '/', label: 'Picks', icon: '🎯' },
  { path: '/props', label: 'Props', icon: '📊' },
  { path: '/paper-trading', label: 'Trade', icon: '💰' },
  { path: '/track-record', label: 'Record', icon: '📈' },
]

export default function BottomNav() {
  return (
    <nav className="bottom-nav">
      {TABS.map(({ path, label, icon }) => (
        <NavLink
          key={path}
          to={path}
          end={path === '/'}
          className={({ isActive }) => `bottom-nav-tab ${isActive ? 'active' : ''}`}
          aria-label={label}
        >
          <span className="bottom-nav-icon">{icon}</span>
          <span className="bottom-nav-label">{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
