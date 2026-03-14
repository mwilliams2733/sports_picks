import type { StrategyData } from '../types';

interface Props { strategies: StrategyData[]; onPromote: (id: number) => void; }

export default function StrategyList({ strategies, onPromote }: Props) {
  return (
    <div>
      {strategies.map(s => (
        <div key={s.id} style={{ padding: '0.75rem', marginBottom: '0.5rem', borderRadius: '6px',
          background: s.is_active ? 'rgba(74,222,128,0.15)' : 'rgba(255,255,255,0.05)',
          border: `1px solid ${s.is_active ? '#4ade80' : 'rgba(255,255,255,0.1)'}`,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <strong>{s.name}</strong> {s.is_active && '(Active)'}
            <div style={{ fontSize: '0.8rem', opacity: 0.6 }}>{s.description}</div>
          </div>
          {!s.is_active && (
            <button onClick={() => onPromote(s.id)} style={{
              background: '#1e40af', color: '#fff', border: 'none',
              borderRadius: '4px', padding: '0.3rem 0.75rem', cursor: 'pointer' }}>Promote</button>
          )}
        </div>
      ))}
    </div>
  );
}
