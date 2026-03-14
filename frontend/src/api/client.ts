import type { PickData, RecordData, DailyData, StrategyData, CompareData } from '../types';

const BASE = '';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function patch<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'PATCH' });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  picks: {
    today: (sport?: string) => get<PickData[]>(`/picks/today${sport ? `?sport=${sport}` : ''}`),
    history: (page = 1) => get<PickData[]>(`/picks/history?page=${page}`),
  },
  stats: {
    record: (sport?: string) => get<RecordData>(`/stats/record${sport ? `?sport=${sport}` : ''}`),
    daily: () => get<DailyData[]>('/stats/daily'),
  },
  backtest: {
    strategies: () => get<StrategyData[]>('/backtest/strategies'),
    create: (data: { name: string; description: string; config: Record<string, unknown> }) =>
      post<StrategyData>('/backtest/strategies', data),
    promote: (id: number) => patch<{ id: number; is_active: boolean }>(`/backtest/strategies/${id}/promote`),
    compare: () => get<CompareData[]>('/backtest/compare'),
  },
};
