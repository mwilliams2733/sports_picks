import type { PickData, RecordData, DailyData, StrategyData, CompareData, PropData, BacktestResult, GameOddsData, AutoTuneResult, UserProfile, PaperPickData, UserStats, RunAllResult } from '../types';

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

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' });
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
    autoTune: (data: { strategy_id: number; start_date: string; end_date: string; optimize_for?: string; apply_best?: boolean }) =>
      post<AutoTuneResult>('/backtest/auto-tune', data),
    runAll: (data: { sport: string; start_date: string; end_date: string }) =>
      post<RunAllResult>('/backtest/run-all', data),
    sportMarkets: (sport?: string) =>
      get<{ key: string; label: string }[]>(`/backtest/sport-markets${sport ? `?sport=${sport}` : ''}`),
  },
  games: {
    today: (sport?: string) => get<GameOddsData[]>(`/games/today${sport ? `?sport=${sport}` : ''}`),
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
  users: {
    list: () => get<UserProfile[]>('/users/'),
    create: (name: string) => post<{ id: number; name: string }>('/users/', { name }),
    get: (id: number) => get<UserProfile>(`/users/${id}`),
    picks: (id: number) => get<PaperPickData[]>(`/users/${id}/picks`),
    placePick: (userId: number, data: { game_id: number; pick_type: string; pick_value: string; odds: number; stake: number; prop_market?: string; prop_player?: string }) =>
      post<{ id: number; result: string | null; payout: number | null; new_balance: number }>(`/users/${userId}/picks`, data),
    grade: () => post<{ graded: number }>('/users/grade', {}),
    stats: (id: number) => get<UserStats>(`/users/${id}/stats`),
    feed: (limit?: number) => get<{ id: number; user_id: number; event_type: string; payload: Record<string, string>; created_at: string }[]>(`/users/feed${limit ? `?limit=${limit}` : ''}`),
    delete: (id: number) => del<{ deleted: boolean; id: number }>(`/users/${id}`),
    placeParlay: (userId: number, data: {
      legs: Array<{ game_id: number; pick_type: string; pick_value: string; odds: number; prop_market?: string; prop_player?: string }>;
      stake: number;
    }) => post<{
      id: number; legs: Array<{ pick_value: string; odds: number; result: string | null }>;
      combined_odds: number; potential_payout: number;
      result: string | null; payout: number | null; new_balance: number;
    }>(`/users/${userId}/parlay`, data),
  },
  pipeline: {
    run: (sport?: string) => post<{
      status: string; active_sports: string[];
      games_stored: number; odds_stored: number; props_stored: number;
      props_analyzed: number; picks_generated: number;
      credits_used: number; credits_remaining_today: number;
      credits_remaining_month: number;
    }>(`/pipeline/run${sport ? `?sport=${sport}` : ''}`, {}),
  },
  credits: {
    get: () => get<{
      monthly_used: number; monthly_limit: number; monthly_remaining: number;
      daily_used: number; daily_target: number;
      api_requests_remaining: number | null;
    }>('/credits/'),
  },
};
