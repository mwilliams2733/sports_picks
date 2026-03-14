export interface PickData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  pick_type: string;
  pick_value: string;
  confidence: number;
  edge_pct: number;
  odds_at_pick: number;
  result?: string | null;
  payout?: number | null;
  home_team?: string;
  away_team?: string;
  matchup?: string;
}

export interface RecordData {
  wins: number;
  losses: number;
  pushes: number;
  total: number;
  win_rate: number;
  roi: number;
  total_profit: number;
}

export interface DailyData {
  date: string;
  wins: number;
  losses: number;
  pushes: number;
  profit: number;
}

export interface StrategyData {
  id: number;
  name: string;
  description: string;
  config: Record<string, unknown>;
  is_active: boolean;
  sport: string | null;
  strategy_type?: string;
}

export interface PropData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  matchup: string;
  bookmaker: string;
  market: string;
  market_label: string;
  player_name: string;
  outcome: string;
  line: number | null;
  odds: number;
  projection: number | null;
  edge_pct: number | null;
  confidence: number | null;
  season_avg: number | null;
  recent_avg: number | null;
  source: string | null;
  is_stale: boolean;
}

export interface CompareData {
  strategy_id: number;
  strategy_name: string;
  run_id: number;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
}

export interface BacktestResult {
  wins: number;
  losses: number;
  total: number;
  hit_rate: number;
  roi: number;
  total_profit: number;
  by_market?: Record<string, { wins: number; losses: number; hit_rate: number }>;
  by_confidence?: Record<string, { wins: number; losses: number; hit_rate: number }>;
}
