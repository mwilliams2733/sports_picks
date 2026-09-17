import { describe, it, expect } from 'vitest';
import { computeCumulativeChartData } from './performanceChart';
import type { DailyData } from '../types';

function makeDay(profit: number): DailyData {
  return { date: '2026-01-01', wins: 0, losses: 0, pushes: 0, profit };
}

describe('computeCumulativeChartData', () => {
  it('preserves the pre-refactor rounding: rounds the running total, not each profit', () => {
    // Hand-computed from the original implementation:
    //   let cumulative = 0;
    //   data.map(d => { cumulative += d.profit; return { ...d, cumulative: Math.round(cumulative * 100) / 100 }; })
    // with data = [{profit: 10.005}, {profit: 10.005}]:
    //   step 1: cumulative = 0 + 10.005 = 10.005 (raw); Math.round(10.005 * 100) / 100 = Math.round(1000.5)/100 = 1001/100 = 10.01
    //   step 2: cumulative = 10.005 + 10.005 = 20.01 (raw, carried unrounded); Math.round(20.01 * 100)/100 = 2001/100 = 20.01
    const data = [makeDay(10.005), makeDay(10.005)];
    const result = computeCumulativeChartData(data);
    expect(result.map(r => r.cumulative)).toEqual([10.01, 20.01]);
  });
});
