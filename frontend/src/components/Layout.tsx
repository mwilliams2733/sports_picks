import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAppStore } from '../stores/appStore'
import { useWebSocket } from '../hooks/useWebSocket'
import BottomNav from './BottomNav'
import MobileMenu from './MobileMenu'

const NAV_ITEMS: [string, string][] = [
  ['/', "Today's Picks"],
  ['/props', 'Player Props'],
  ['/backtesting', 'Backtesting'],
  ['/track-record', 'Track Record'],
  ['/paper-trading', 'Paper Trading'],
  ['/faq', 'FAQ'],
]

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf']

export default function Layout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const { sport, setSport } = useAppStore()
  useWebSocket()

  return (
    <>
      <header className="header">
        <div className="header-left">
          <button
            className="hamburger-btn"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Menu"
          >
            ☰
          </button>
          <div className="header-logo">
            <span className="header-logo-accent">SP</span> Sports Picks
          </div>
        </div>
        <nav className="header-nav">
          {NAV_ITEMS.map(([path, label]) => (
            <NavLink
              key={path}
              to={path}
              end={path === '/'}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="header-sport-filter">
          {SPORTS.map((s) => (
            <button
              key={s}
              className={`sport-filter-btn ${sport === s ? 'active' : ''}`}
              onClick={() => setSport(s)}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <MobileMenu open={menuOpen} onClose={() => setMenuOpen(false)} />
      <main className="main-content">
        <Outlet />
      </main>
      <BottomNav />
    </>
  )
}
