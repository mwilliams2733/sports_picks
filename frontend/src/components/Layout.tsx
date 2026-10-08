import { useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { useAppStore } from '../stores/appStore'
import { useWebSocket } from '../hooks/useWebSocket'
import MainTabs from './MainTabs'
import PlayerChip from './PlayerChip'
import ResearchMenu from './ResearchMenu'
import { RESEARCH_LINKS } from '../lib/nav'

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb']

export default function Layout() {
  const [researchOpen, setResearchOpen] = useState(false)
  const { sport, setSport } = useAppStore()
  const { pathname } = useLocation()
  const onResearch = RESEARCH_LINKS.some(([path]) => pathname.startsWith(path))
  const toggle = () => setResearchOpen(o => !o)
  useWebSocket()

  return (
    <>
      <header className="sb-topbar">
        <Link to="/" className="sb-wordmark" aria-label="METRIC EDGE home">METRIC <span>EDGE</span></Link>
        <MainTabs className="sb-topnav" researchActive={onResearch} onResearch={toggle} />
        <PlayerChip />
      </header>
      {onResearch && (
        <div className="header-sport-filter" aria-label="Sport filter" style={{ display: 'flex', padding: '0.5rem 1rem' }}>
          {SPORTS.map((s) => (
            <button key={s} className={`sport-filter-btn ${sport === s ? 'active' : ''}`} onClick={() => setSport(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      )}
      <ResearchMenu open={researchOpen} onClose={() => setResearchOpen(false)} />
      <main className="main-content"><Outlet /></main>
      <MainTabs className="sb-tabbar" researchActive={onResearch} onResearch={toggle} />
    </>
  )
}
