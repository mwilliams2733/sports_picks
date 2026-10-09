import { useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { useAppStore } from '../stores/appStore'
import { useWebSocket } from '../hooks/useWebSocket'
import MainTabs from './MainTabs'
import PlayerChip from './PlayerChip'
import BetSlip from './BetSlip'
import Celebrations from './Celebrations'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { splitTickets } from '../lib/bets'
import { useSlip } from '../stores/slipStore'
import ResearchMenu from './ResearchMenu'
import { RESEARCH_LINKS } from '../lib/nav'

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb']

export default function Layout() {
  const [researchOpen, setResearchOpen] = useState(false)
  const { sport, setSport } = useAppStore()
  const { pathname } = useLocation()
  const onResearch = RESEARCH_LINKS.some(([path]) => pathname.startsWith(path))
  const toggle = () => setResearchOpen(o => !o)
  const slipVisible = useSlip(s => s.legs.length > 0 || s.receipt !== null)
  const me = useCurrentPlayer()
  const myBets = useMyBets(me?.id)
  const openCount = splitTickets(myBets.data?.tickets ?? []).open.length
  useWebSocket()

  return (
    <>
      <header className="sb-topbar">
        <Link to="/" className="sb-wordmark" aria-label="METRIC EDGE home">METRIC <span>EDGE</span></Link>
        <MainTabs className="sb-topnav" researchActive={onResearch} onResearch={toggle} openCount={openCount} />
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
      <main className={`main-content${slipVisible ? ' with-slip' : ''}`}><Outlet /></main>
      <BetSlip />
      <Celebrations />
      <MainTabs className="sb-tabbar" researchActive={onResearch} onResearch={toggle} openCount={openCount} />
    </>
  )
}
