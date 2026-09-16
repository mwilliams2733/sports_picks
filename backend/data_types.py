from dataclasses import dataclass, field
from datetime import date

@dataclass
class TeamStats:
    point_diff: float
    home_record: tuple[int, int]
    away_record: tuple[int, int]
    last_n_record: tuple[int, int]
    offensive_rating: float
    defensive_rating: float
    pace: float
    strength_of_schedule: float
    elo_rating: float
    rest_days: int
    turnover_margin: float | None = None
    red_zone_pct: float | None = None
    conference_strength: float | None = None
    is_schedule_fatigued: bool = False  # 3rd game in 4 nights (NBA)
    is_lookahead_spot: bool = False     # weak opponent now, big game next
    schedule_fatigue_score: float = 0.0  # 0 to 1 severity
    pitcher_skill_score: float | None = None  # MLB-only; 0.5 = league-average

@dataclass
class OddsSnapshot:
    bookmaker: str
    moneyline_home: int
    moneyline_away: int
    spread_home: float
    spread_away: float
    over_under: float

@dataclass(frozen=True)
class PickFactor:
    """One reason a strategy favored a side.

    Only emit a factor when the strategy actually computed the underlying
    signal — these strings are shown to readers as the reasoning behind a
    pick, so a factor that was not used is a fabrication.
    """
    code: str      # see backend/analysis/rationale.py FACTOR_TEMPLATES
    side: str      # "home" | "away" | "over" | "under"
    strength: str  # "slight" | "moderate" | "strong"

@dataclass
class Pick:
    game_id: int
    pick_type: str
    pick_value: str
    confidence: int
    edge_pct: float
    model_probability: float
    implied_probability: float
    odds_at_pick: int
    suggested_unit_size: float = 1.0
    factors: list[PickFactor] = field(default_factory=list)

@dataclass
class PropAnalysis:
    player_name: str
    market: str
    line: float
    outcome: str          # "Over" or "Under"
    season_avg: float | None
    recent_avg: float | None
    projection: float
    edge_pct: float
    confidence: int
    source: str
    is_stale: bool
    game_id: int
    odds: int
    bookmaker: str

@dataclass
class GameData:
    game_id: int
    sport: str
    date: date
    home_team_id: int
    away_team_id: int
    home_stats: TeamStats
    away_stats: TeamStats
    odds: list[OddsSnapshot] = field(default_factory=list)
    week: int | None = None
    home_fighter: "FighterStats | None" = None
    away_fighter: "FighterStats | None" = None

@dataclass
class FighterStats:
    elo_rating: float
    recent_form_score: float
    opponent_avg_elo: float | None
    fights_count: int
    days_since_last_fight: int | None
