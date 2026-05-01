"""Sport-specific empirical constants for CDF-based edge calculations."""

# Standard deviation of game-to-game scoring margin (home_score - away_score)
POINT_DIFF_STD = {
    "nba": 12.0,
    "nfl": 13.5,
    "ncaab": 11.0,
    "ncaaf": 17.0,
    "boxing": 12.0,
    "mma": 12.0,
    "mlb": 3.5,
}

# Standard deviation of game-to-game total points
TOTAL_POINTS_STD = {
    "nba": 15.0,
    "nfl": 13.0,
    "ncaab": 12.0,
    "ncaaf": 16.0,
    "boxing": 15.0,
    "mma": 15.0,
    "mlb": 4.0,
}

# Home court/field advantage in ELO points
HOME_ADVANTAGE_ELO = {
    "nba": 100,
    "nfl": 48,
    "ncaab": 120,
    "ncaaf": 65,
    "boxing": 0,
    "mma": 0,
    "mlb": 24,
}

# Default home win probability (used as HCA weight in fallback models)
HOME_WIN_RATE = {
    "nba": 0.60,
    "nfl": 0.57,
    "ncaab": 0.67,
    "ncaaf": 0.62,
    "boxing": 0.50,
    "mma": 0.50,
    "mlb": 0.54,
}


def get_point_diff_std(sport: str) -> float:
    return POINT_DIFF_STD.get(sport, 12.0)


def get_total_points_std(sport: str) -> float:
    return TOTAL_POINTS_STD.get(sport, 15.0)


def get_home_advantage_elo(sport: str) -> int:
    return HOME_ADVANTAGE_ELO.get(sport, 100)


def get_home_win_rate(sport: str) -> float:
    return HOME_WIN_RATE.get(sport, 0.57)
