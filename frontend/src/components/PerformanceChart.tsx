import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import type { DailyData } from '../types';

interface Props { data: DailyData[]; }

export default function PerformanceChart({ data }: Props) {
  let cumulative = 0;
  const chartData = data.map(d => {
    cumulative += d.profit;
    return { ...d, cumulative: Math.round(cumulative * 100) / 100 };
  });
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData}>
        <XAxis dataKey="date" stroke="#64748b" fontSize={12} />
        <YAxis stroke="#64748b" fontSize={12} />
        <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155' }} />
        <Legend />
        <Line type="monotone" dataKey="cumulative" stroke="#4ade80" name="Cumulative ROI" dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
