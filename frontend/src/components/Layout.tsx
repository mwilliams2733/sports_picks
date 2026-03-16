import { NavLink, Outlet } from 'react-router-dom';

const NAV_ITEMS = [
  { path: '/', label: "Today's Picks" },
  { path: '/props', label: 'Player Props' },
  { path: '/backtesting', label: 'Backtesting' },
  { path: '/track-record', label: 'Track Record' },
  { path: '/paper-trading', label: 'Paper Trading' },
  { path: '/faq', label: 'FAQ' },
];

export default function Layout() {
  return (
    <div id="app">
      <header className="header">
        <div className="header-logo">
          <span className="header-logo-accent">SP</span> Sports Picks
        </div>
        <nav className="header-nav">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              end={item.path === '/'}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="main-content"><Outlet /></main>
    </div>
  );
}
