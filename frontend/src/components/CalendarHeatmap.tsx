import type { DailyData } from '../types';

interface Props { data: DailyData[]; }

export default function CalendarHeatmap({ data }: Props) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
      {data.map(d => {
        const total = d.wins + d.losses;
        const winRate = total > 0 ? d.wins / total : 0.5;
        const color = total === 0 ? 'var(--bg-elevated)'
          : winRate >= 0.7 ? 'var(--green)'
          : winRate >= 0.5 ? '#86efac'
          : winRate >= 0.3 ? '#fca5a5'
          : 'var(--red)';
        return (
          <div key={d.date} title={`${d.date}: ${d.wins}W ${d.losses}L`} style={{
            width: '18px', height: '18px', borderRadius: '3px', background: color,
            transition: 'transform 150ms ease',
          }}
          onMouseEnter={e => (e.currentTarget.style.transform = 'scale(1.4)')}
          onMouseLeave={e => (e.currentTarget.style.transform = 'scale(1)')}
          />
        );
      })}
    </div>
  );
}
