import { NavLink } from 'react-router-dom'

interface Props {
  open: boolean
  onClose: () => void
}

const MENU_ITEMS = [
  { path: '/backtesting', label: 'Backtesting' },
  { path: '/faq', label: 'FAQ' },
  { path: '/admin', label: 'Admin' },
]

export default function MobileMenu({ open, onClose }: Props) {
  if (!open) return null
  return (
    <>
      <div className="mobile-menu-overlay" onClick={onClose} />
      <div className="mobile-menu">
        <div className="mobile-menu-header">
          <span>Menu</span>
          <button className="mobile-menu-close" onClick={onClose}>✕</button>
        </div>
        {MENU_ITEMS.map(({ path, label }) => (
          <NavLink
            key={path}
            to={path}
            className={({ isActive }) => `mobile-menu-item ${isActive ? 'active' : ''}`}
            onClick={onClose}
          >
            {label}
          </NavLink>
        ))}
      </div>
    </>
  )
}
