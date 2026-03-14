import type { DailyData } from '../types';

interface Props { data: DailyData[]; }

export default function CalendarHeatmap({ data }: Props) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
      {data.map(d => {
        const total = d.wins + d.losses;
        const winRate = total > 0 ? d.wins / total : 0.5;
        const color = total === 0 ? '#1e293b'
          : winRate >= 0.7 ? '#22c55e'
          : winRate >= 0.5 ? '#86efac'
          : winRate >= 0.3 ? '#fca5a5'
          : '#ef4444';
        return (
          <div key={d.date} title={`${d.date}: ${d.wins}W ${d.losses}L`} style={{
            width: '14px', height: '14px', borderRadius: '2px', background: color }} />
        );
      })}
    </div>
  );
}
