import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import type { CalibrationData } from '../types';

interface Props {
  data: CalibrationData;
}

interface Point {
  predicted: number;
  actual: number;
  tier: number;
  sample_size: number;
}

interface TipItem { payload?: Point }

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TipItem[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div style={{
      background: '#1e293b',
      border: '1px solid rgba(148, 163, 184, 0.15)',
      borderRadius: 8,
      padding: '0.5rem 0.75rem',
      fontSize: '0.85rem',
      boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      color: '#e2e8f0',
    }}>
      <div style={{ color: '#94a3b8', marginBottom: 4 }}>Confidence tier {p.tier}</div>
      <div>Predicted: <span style={{ color: '#94a3b8' }}>{p.predicted.toFixed(0)}%</span></div>
      <div>Actual: <span style={{ color: p.actual >= p.predicted ? '#22c55e' : '#ef4444' }}>{p.actual.toFixed(0)}%</span></div>
      <div style={{ color: '#94a3b8', marginTop: 4 }}>n = {p.sample_size}</div>
    </div>
  );
}

export default function CalibrationChart({ data }: Props) {
  if (!data.tiers.length) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">No calibration data yet</div>
        <div className="text-muted">
          Calibration appears once picks are graded. {data.total_graded === 0 ? 'No graded picks found.' : `${data.total_graded} picks graded but none in scoring tiers.`}
        </div>
      </div>
    );
  }

  const points: Point[] = data.tiers.map(t => ({
    predicted: t.predicted_win_rate * 100,
    actual: t.actual_win_rate * 100,
    tier: t.tier,
    sample_size: t.sample_size,
  }));

  return (
    <div>
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart margin={{ top: 12, right: 24, bottom: 12, left: 8 }}>
          <XAxis
            type="number"
            dataKey="predicted"
            domain={[40, 80]}
            stroke="#475569"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            label={{ value: 'Predicted win rate (%)', position: 'insideBottom', offset: -2, fill: '#94a3b8', fontSize: 11 }}
          />
          <YAxis
            type="number"
            dataKey="actual"
            domain={[0, 100]}
            stroke="#475569"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            label={{ value: 'Actual win rate (%)', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 11 }}
          />
          <ZAxis type="number" dataKey="sample_size" range={[80, 400]} />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
            stroke="#64748b"
            strokeDasharray="4 4"
            ifOverflow="extendDomain"
            label={{ value: 'Perfect calibration', position: 'insideTopRight', fill: '#64748b', fontSize: 10 }}
          />
          <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
          <Scatter name="Confidence tier" data={points} fill="#22c55e" />
        </ScatterChart>
      </ResponsiveContainer>
      {data.brier_score !== null && (
        <div style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: '#94a3b8' }}>
          Brier score: <span className="mono" style={{ color: '#e2e8f0' }}>{data.brier_score.toFixed(4)}</span>
          <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem' }}>
            (lower is better; 0.25 = no skill, 0 = perfect)
          </span>
          <span style={{ marginLeft: '0.5rem' }}>· {data.total_graded} graded picks</span>
        </div>
      )}
    </div>
  );
}
