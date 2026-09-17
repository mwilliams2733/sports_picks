import type { DailyData } from '../types';

// Extracted from PerformanceChart so the component file only exports the
// component (react-refresh/only-export-components).
export function computeCumulativeChartData(data: DailyData[]) {
  const { rows } = data.reduce<{ rows: (DailyData & { cumulative: number })[]; running: number }>(
    (acc, d) => {
      const running = acc.running + d.profit;
      acc.rows.push({ ...d, cumulative: Math.round(running * 100) / 100 });
      acc.running = running;
      return acc;
    },
    { rows: [], running: 0 }
  );
  return rows;
}
