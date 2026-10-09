export interface PickData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  pick_type: string;
  pick_value: string;
  /** The unresolved label ("HOME ML", "AWAY +1.5") pick_value was rewritten
   *  from -- what quotes.legFromPick needs to map the pick to a bettable
   *  side; pick_value is display-only (team/fighter names) and cannot be
   *  parsed back into a side. */
  stored_pick_value?: string;
  confidence: number;
  edge_pct: number;
  odds_at_pick: number;
  /** Kelly stake in units, where one unit is 1% of bankroll.
   *  0 means the sizer declined the bet; null for picks made before it was
   *  persisted. */
  suggested_unit_size?: number | null;
  result?: string | null;
  payout?: number | null;
  clv_pct?: number | null;
  clv_points?: number | null;
  home_team?: string;
  away_team?: string;
  matchup?: string;
  /** Only for pick_type === "prop" -- the market KEY ("player_pass_yds") and
   *  player name a prop pick needs to become a bettable leg (the slip's
   *  prop_market/prop_player). Absent for every other pick_type. */
  prop_market?: string;
  prop_player?: string;
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

export interface LastMeeting {
  date: string;
  home_score: number;
  away_score: number;
  // 'home' / 'away' relative to TODAY's matchup (already corner-corrected by API).
  winner: 'home' | 'away' | 'tie';
}

export interface GameOddsData {
  id: number;
  sport: string;
  date: string;
  status: string;
  start_time: string | null;
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
  last_meeting: LastMeeting | null;
  home_l10_record: string;
  away_l10_record: string;
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
  /** Settled balance minus stakes on bets still open: what can be bet now. */
  available_balance: number;
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
  has_pin?: boolean;
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

export interface LeaderboardRow {
  id: number | null; name: string; is_model: boolean;
  wins: number; losses: number; pushes: number; pending: number; n: number; n_eff: number;
  win_rate: number | null; roi: number | null; shrunk_roi: number | null;
  profit: number; ranked: boolean;
}

export interface EmailedSummary {
  label: string; wins: number; losses: number; pushes: number; pending: number; n: number;
  win_rate: number | null; range_low: number | null; range_high: number | null;
  break_even: number | null; profit: number; staked: number; roi: number | null;
  verdict: 'above' | 'below' | null;
}

export interface EmailedGroups {
  kind: 'game' | 'prop'; by: 'week' | 'month' | 'stars';
  groups: EmailedSummary[]; total: EmailedSummary;
}

export interface EmailedTrend {
  kind: 'game' | 'prop';
  points: { date: string; units: number }[];
  max_drawdown: number; longest_losing_streak: number;
}

export type GamePickType = 'moneyline' | 'spread' | 'over_under';
export type GameSide = 'HOME' | 'AWAY' | 'Over' | 'Under';

export interface QuoteFields {
  pick_type: string;
  pick_value: string;
  odds: number;
  line: number | null;
  quoted_at: string;
  prop_player: string | null;
  prop_market: string | null;
}
// A refusal has no price field, so reading `odds` requires narrowing on
// `available` first -- the compiler stops a refused quote being charged.
type Refusal = { available: false; reason: string; message: string };
type GameKey = { pick_type: GamePickType; side: GameSide };
type PropKey = { prop_player: string; prop_market: string; market_label: string;
  outcome: 'Over' | 'Under'; line: number };

export type AvailableGameQuote = GameKey & { available: true } & Omit<QuoteFields, 'pick_type'>;
export type GameQuote = AvailableGameQuote | (GameKey & Refusal);
export type AvailablePropQuote = PropKey & { available: true; pick_type: 'prop'; pick_value: string;
  odds: number; quoted_at: string };
export type PropQuote = AvailablePropQuote | (PropKey & Refusal);

export type GameLeg = { game_id: number; pick_type: GamePickType; side: GameSide };
export type PropLeg = { game_id: number; pick_type: 'prop'; prop_player: string; prop_market: string;
  outcome: 'Over' | 'Under'; line: number };
export type BetLeg = GameLeg | PropLeg;

export interface PlacedPick {
  id: number; result: string | null; payout: number | null; new_balance: number;
  pick_value: string; odds: number; line: number | null; quoted_at: string;
}

export interface ModelPick { pick_type: GamePickType; pick_value: string; odds: number | null; edge_pct: number }

export interface BoardGame {
  id: number; sport: string; date: string; start_time: string | null;
  home_team: string; away_team: string; quotes: GameQuote[];
  prop_count: number | null; model_pick: ModelPick | null;
}


/** A bet as the slip sends it: the leg plus the price and line the player saw
 *  (both omitted under "Accept any odds changes"). Props send no line -- the
 *  line is part of a prop bet. */
export type BetRequest = BetLeg & { expected_odds?: number; expected_line?: number | null }

/** A side a tile or a model pick puts on the slip. `odds`/`line` are the
 *  price the player saw, sent as expected_odds/expected_line. */
export interface SlipSelection {
  leg: BetLeg; label: string; gameLabel: string; startTime: string | null;
  odds: number; line: number | null; homeTeam: string; awayTeam: string;
}

export interface SlipLeg extends SlipSelection {
  stake: number
  /** The price the player saw before the server answered price_moved. */
  movedFrom?: { odds: number; line: number | null } | null
  /** The server's refusal for this leg on the last attempt. */
  error?: string | null
  /** A tailed leg no book will price now: shown for reference, never placed. */
  locked?: boolean
}

export interface ReceiptBet {
  id: number; kind: 'single' | 'parlay'; labels: string[]; stake: number;
  /** What the server charged, and what the slip showed when that differed. */
  odds: number; shownOdds: number | null; toWin: number;
}

export interface Receipt { bets: ReceiptBet[]; legs: SlipLeg[]; placedAt: string }

export interface TicketGame {
  id: number; sport: string; home_team: string; away_team: string; start_time: string | null;
  status: string; home_score: number | null; away_score: number | null; live_detail: string | null;
}
export interface TicketLeg {
  pick_type: string; pick_value: string; odds: number; prop_player: string | null;
  prop_market: string | null; result: string | null; game: TicketGame;
}
/** A ticket's cash-out field: null once settled (sportsbook spec §9). */
export interface CashOutView { available: boolean; offer?: number; reason?: string; message?: string }
export interface Ticket {
  kind: 'straight' | 'parlay'; id: number; stake: number; odds: number; to_win: number;
  result: string | null; payout: number | null; created_at: string; sgp: boolean; legs: TicketLeg[];
  cash_out?: CashOutView | null;
}
export interface BetsSummary { available: number; balance: number; open_stakes: number; today_pl: number }
export interface MyBetsData { summary: BetsSummary; tickets: Ticket[] }

/** A placed bet's leg as the feed carries it: the bet request plus display. */
export type FeedLeg = BetLeg & {
  label: string; game_label: string; start_time: string | null;
  home_team: string; away_team: string; odds: number;
  /** The line the friend got (a prop request already has its own `line`). */
  quoted_line: number | null;
}
/** Older events carry only user_name/message; every other field is optional. */
export interface FeedPayload {
  user_name?: string; message?: string; bet_id?: number; kind?: 'straight' | 'parlay';
  legs?: FeedLeg[]; result?: string; payout?: number; stake?: number;
}
export interface FeedItem {
  id: number; user_id: number | null; event_type: string; payload: FeedPayload; created_at: string;
}
