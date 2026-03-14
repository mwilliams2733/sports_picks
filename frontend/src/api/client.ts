import type { PickData, RecordData, DailyData, StrategyData, CompareData, PropData, BacktestResult } from '../types';

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

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
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
    update: (id: number, data: { description?: string; config?: Record<string, unknown> }) =>
      put<{ id: number; updated: boolean }>(`/backtest/strategies/${id}`, data),
    promote: (id: number) => patch<{ id: number; is_active: boolean }>(`/backtest/strategies/${id}/promote`),
    compare: () => get<CompareData[]>('/backtest/compare'),
    run: (data: { strategy_id: number; start_date: string; end_date: string }) =>
      post<BacktestResult>('/backtest/run', data),
  },
  props: {
    today: (sport?: string, market?: string) => {
      const params = new URLSearchParams();
      if (sport) params.set('sport', sport);
      if (market) params.set('market', market);
      const qs = params.toString();
      return get<PropData[]>(`/props/today${qs ? `?${qs}` : ''}`);
    },
    markets: () => get<{ key: string; label: string }[]>('/props/markets'),
  },
  pipeline: {
    run: () => post<{ status: string; games: number; stats_fetched: number;
      props_analyzed: number; picks_generated: number }>('/pipeline/run', {}),
  },
};
