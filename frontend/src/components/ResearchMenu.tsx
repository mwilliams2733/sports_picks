import { NavLink } from 'react-router-dom'
import { RESEARCH_LINKS } from '../lib/nav'

export default function ResearchMenu({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <>
      <div className="sb-research-overlay" onClick={onClose} />
      <nav className="sb-research" aria-label="Research">
        {RESEARCH_LINKS.map(([path, label]) => (
          <NavLink key={path} to={path} onClick={onClose}
            className={({ isActive }) => (isActive ? 'active' : '')}>{label}</NavLink>
        ))}
      </nav>
    </>
  )
}
