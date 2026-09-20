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
    #: Mean points scored and conceded over the team's recent prior games,
    #: point-in-time. These are the totals model's only inputs.
    #:
    #: None means no scoring history, and that is not the same as zero: a
    #: predicted 0-0 game is worse than declining to predict. The totals
    #: branch gates on both sides having both values.
    points_for: float | None = None
    points_against: float | None = None
    #: The same rates restricted to one venue, excluding neutral-site games.
    #: None when that venue has fewer than MIN_VENUE_GAMES of history.
    points_for_home: float | None = None
    points_against_home: float | None = None
    points_for_away: float | None = None
    points_against_away: float | None = None
    #: The same rates with each prior game shifted by how much that opponent
    #: usually concedes or scores, relative to the league.
    points_for_adj: float | None = None
    points_against_adj: float | None = None

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
    #: True when neither side is hosting (tournament bracket, neutral
    #: showcase). Home advantage does not apply, so the model's per-sport
    #: home slot must not fire. Mirrors `Game.neutral_site`.
    neutral_site: bool = False
    #: ESPN's season phase for this game: "regular", "postseason",
    #: "preseason", "allstar" or "unknown". The totals model corrects a
    #: measured per-sport bias on it.
    season_type: str = "unknown"
    home_fighter: "FighterStats | None" = None
    away_fighter: "FighterStats | None" = None

@dataclass
class FighterStats:
    elo_rating: float
    recent_form_score: float
    opponent_avg_elo: float | None
    fights_count: int
    days_since_last_fight: int | None
