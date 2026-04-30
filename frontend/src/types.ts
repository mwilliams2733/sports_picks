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
  clv_pct?: number | null;
  clv_points?: number | null;
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

export interface GameOddsData {
  id: number;
  sport: string;
  date: string;
  status: string;
  home_team: string;
  away_team: string;
  home_team_name: string;
  away_team_name: string;
  home_score: number | null;
  away_score: number | null;
  moneyline_home: number | null;
  moneyline_away: number | null;
  spread_home: number | null;
  over_under: number | null;
  bookmaker: string | null;
  odds_count: number;
}

export interface AutoTuneResult {
  strategy_name?: string;
  sport?: string;
  games_tested?: number;
  configs_tested: number;
  best_config: Record<string, unknown> | null;
  best_result: {
    wins: number;
    losses: number;
    total: number;
    win_rate?: number;
    hit_rate?: number;
    roi: number;
    total_profit: number;
  } | null;
  all_results: Array<{
    config: Record<string, unknown>;
    wins: number;
    losses: number;
    total: number;
    win_rate?: number;
    hit_rate?: number;
    roi: number;
  }>;
  applied: boolean;
  strategy_id?: number;
  error?: string;
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

export interface VariantResult {
  wins: number;
  losses: number;
  pushes?: number;
  total: number;
  win_rate?: number;
  hit_rate?: number;
  roi: number;
  total_profit: number;
  picks?: Array<Record<string, unknown>>;
  by_market?: Record<string, { wins: number; losses: number; hit_rate?: number }>;
  by_confidence?: Record<number, { wins: number; losses: number }>;
}

export interface RunAllResult {
  sport: string;
  start_date: string;
  end_date: string;
  games_count: number;
  variants: Record<string, VariantResult>;
  sport_markets: Array<{ key: string; label: string }>;
}

export interface UserProfile {
  id: number;
  name: string;
  starting_balance: number;
  current_balance: number;
  total_wagered: number;
  profit: number;
  roi: number;
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  win_rate: number;
  current_streak: number;
  best_streak: number;
  streak_type: string;
  created_at?: string;
}

export interface PaperPickData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  status: string;
  pick_type: string;
  pick_value: string;
  odds: number;
  stake: number;
  result: string | null;
  payout: number | null;
  prop_market?: string;
  prop_player?: string;
  created_at: string;
}

export interface PeriodStats {
  wins: number;
  losses: number;
  pushes: number;
  total: number;
  win_rate: number;
  profit: number;
  roi: number;
}

export interface UserStats {
  today: PeriodStats;
  this_week: PeriodStats;
  this_month: PeriodStats;
  all_time: PeriodStats;
  daily_breakdown: Array<PeriodStats & { date: string }>;
}

export interface CalibrationTier {
  tier: number;
  predicted_win_rate: number;
  actual_win_rate: number;
  sample_size: number;
}

export interface CalibrationData {
  tiers: CalibrationTier[];
  total_graded: number;
  brier_score: number | null;
}
