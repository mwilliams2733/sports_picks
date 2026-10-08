import type { PickData, RecordData, DailyData, StrategyData, CompareData, PropData, BacktestResult, GameOddsData, AutoTuneResult, UserProfile, PaperPickData, UserStats, RunAllResult, CalibrationData, LeaderboardRow, EmailedGroups, EmailedTrend, GameQuote, PropQuote, PlacedPick, BoardGame, BetRequest } from '../types';
import { getOwnerKey } from '../lib/secrets';

const BASE = '';

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    const rawDetail = typeof body === 'object' && body !== null && 'detail' in body
      ? (body as { detail: unknown }).detail
      : undefined;
    // FastAPI/pydantic validation errors send `detail` as a list of
    // { msg, ... } objects rather than a string; use the first message.
    const detail = Array.isArray(rawDetail)
      ? (typeof rawDetail[0] === 'object' && rawDetail[0] !== null && 'msg' in rawDetail[0]
          ? String((rawDetail[0] as { msg: unknown }).msg)
          : undefined)
      : typeof rawDetail === 'object' && rawDetail !== null && 'message' in rawDetail
        // A structured refusal (409 price_moved) carries its own message.
        ? String((rawDetail as { message: unknown }).message)
      : rawDetail != null ? String(rawDetail) : undefined;
    super(detail || `API error: ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export function getErrorMessage(e: unknown): string {
  return e instanceof Error ? e.message : 'Unknown error';
}

async function throwApiError(res: Response): Promise<never> {
  let body: unknown = undefined;
  try {
    body = await res.json();
  } catch {
    // response body wasn't JSON (or was empty) — fall back to status-only message
  }
  throw new ApiError(res.status, body);
}

function writeHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...extra };
  const owner = getOwnerKey();
  if (owner) headers['X-Owner-Key'] = owner;
  return headers;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) return throwApiError(res);
  return res.json();
}

async function post<T>(path: string, body: unknown, extra: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: writeHeaders(extra),
    body: JSON.stringify(body),
  });
  if (!res.ok) return throwApiError(res);
  return res.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: writeHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) return throwApiError(res);
  return res.json();
}

async function patch<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'PATCH', headers: writeHeaders() });
  if (!res.ok) return throwApiError(res);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE', headers: writeHeaders() });
  if (!res.ok) return throwApiError(res);
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
    calibration: (sport?: string) => get<CalibrationData>(`/stats/calibration${sport ? `?sport=${sport}` : ''}`),
    emailed: (kind: 'game' | 'prop', by: 'week' | 'month' | 'stars') => get<EmailedGroups>(`/stats/emailed?kind=${kind}&by=${by}`),
    emailedTrend: (kind: 'game' | 'prop') => get<EmailedTrend>(`/stats/emailed/trend?kind=${kind}`),
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
    create: (name: string, pin: string) => post<{ id: number; name: string }>('/users/', { name, pin }),
    get: (id: number) => get<UserProfile>(`/users/${id}`),
    picks: (id: number) => get<PaperPickData[]>(`/users/${id}/picks`),
    placePick: (userId: number, data: BetRequest & { stake: number }, pin: string) =>
      post<PlacedPick>(`/users/${userId}/picks`, data, { 'X-Player-Pin': pin }),
    grade: () => post<{ graded: number }>('/users/grade', {}),
    stats: (id: number) => get<UserStats>(`/users/${id}/stats`),
    feed: (limit?: number) => get<{ id: number; user_id: number; event_type: string; payload: Record<string, string>; created_at: string }[]>(`/users/feed${limit ? `?limit=${limit}` : ''}`),
    delete: (id: number) => del<{ deleted: boolean; id: number }>(`/users/${id}`),
    setPin: (id: number, pin: string) => put<{ id: number; pin_set: boolean }>(`/users/${id}/pin`, { pin }),
    leaderboard: () => get<LeaderboardRow[]>('/users/leaderboard'),
    placeParlay: (userId: number, data: { legs: BetRequest[]; stake: number }, pin: string) => post<{
      id: number;
      legs: Array<{ pick_value: string; odds: number; line: number | null; quoted_at: string; result: string | null }>;
      combined_odds: number; potential_payout: number;
      result: string | null; payout: number | null; new_balance: number;
    }>(`/users/${userId}/parlay`, data, { 'X-Player-Pin': pin }),
  },
  paper: {
    quotes: (gameId: number) => get<{ game_id: number; quotes: GameQuote[] }>(`/paper/quotes?game_id=${gameId}`),
    propQuotes: (gameId: number) =>
      get<{ game_id: number; quotes: PropQuote[] }>(`/paper/prop-quotes?game_id=${gameId}`),
    board: () => get<{ games: BoardGame[] }>('/paper/board?days=7'),
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
