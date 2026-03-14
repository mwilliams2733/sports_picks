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

@dataclass
class OddsSnapshot:
    bookmaker: str
    moneyline_home: int
    moneyline_away: int
    spread_home: float
    spread_away: float
    over_under: float

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
