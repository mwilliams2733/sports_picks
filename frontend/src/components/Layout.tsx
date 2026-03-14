import { NavLink, Outlet } from 'react-router-dom';

const NAV_ITEMS = [
  { path: '/', label: "Today's Picks" },
  { path: '/backtesting', label: 'Backtesting' },
  { path: '/track-record', label: 'Track Record' },
];

export default function Layout() {
  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0' }}>
      <header style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '1rem 2rem', borderBottom: '1px solid #1e293b'
      }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold', margin: 0 }}>Sports Picks</h1>
        <nav style={{ display: 'flex', gap: '1.5rem' }}>
          {NAV_ITEMS.map(item => (
            <NavLink key={item.path} to={item.path}
              style={({ isActive }) => ({
                color: isActive ? '#38bdf8' : '#94a3b8',
                textDecoration: 'none', fontWeight: isActive ? 'bold' : 'normal',
              })}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main style={{ padding: '2rem' }}><Outlet /></main>
    </div>
  );
}
