import type { StrategyData } from '../types';

interface Props {
  strategies: StrategyData[];
  onPromote: (id: number) => void;
  onEdit?: (strategy: StrategyData) => void;
}

export default function StrategyList({ strategies, onPromote, onEdit }: Props) {
  return (
    <div>
      {strategies.map(s => (
        <div key={s.id} className={`strategy-card${s.is_active ? ' active' : ''}`}>
          <div>
            <span className="strategy-name">{s.name}</span>
            {s.is_active && <span className="strategy-badge badge badge-green">Active</span>}
            {s.sport && <span className="strategy-badge badge badge-default">{s.sport.toUpperCase()}</span>}
            <div className="strategy-desc">{s.description}</div>
          </div>
          <div style={{ display: 'flex', gap: '0.35rem' }}>
            {onEdit && (
              <button className="btn btn-ghost btn-sm" onClick={() => onEdit(s)}>Edit</button>
            )}
            {!s.is_active && (
              <button className="btn btn-primary btn-sm" onClick={() => onPromote(s.id)}>Promote</button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
