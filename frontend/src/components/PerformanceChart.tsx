import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import type { DailyData } from '../types';
import { computeCumulativeChartData } from '../lib/performanceChart';

interface Props { data: DailyData[]; }

export default function PerformanceChart({ data }: Props) {
  const chartData = computeCumulativeChartData(data);
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={chartData}>
        <XAxis dataKey="date" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
        <YAxis stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
        <Tooltip
          contentStyle={{
            background: '#1e293b',
            border: '1px solid rgba(148, 163, 184, 0.15)',
            borderRadius: '8px',
            fontSize: '0.85rem',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
        <Line type="monotone" dataKey="cumulative" stroke="#22c55e" name="Cumulative ROI" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
