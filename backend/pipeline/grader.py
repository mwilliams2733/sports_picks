import re
from backend.analysis.odds_utils import calculate_payout

# Map prop market keys to PlayerStat field names
MARKET_STAT_MAP = {
    "player_points": ["points"],
    "player_rebounds": ["rebounds"],
    "player_assists": ["assists"],
    "player_threes": ["threes"],
    "player_blocks": ["blocks"],
    "player_steals": ["steals"],
    "player_turnovers": ["turnovers"],
    "player_points_rebounds_assists": ["points", "rebounds", "assists"],
    "player_points_rebounds": ["points", "rebounds"],
    "player_points_assists": ["points", "assists"],
    "player_rebounds_assists": ["rebounds", "assists"],
    "player_pass_yds": ["pass_yards"],
    "player_rush_yds": ["rush_yards"],
    "player_reception_yds": ["rec_yards"],
    "player_pass_tds": ["touchdowns"],
    "player_receptions": ["receptions"],
    "player_anytime_td": ["touchdowns"],
}


def grade_pick(pick_type: str, pick_value: str, home_score: int, away_score: int, odds_at_pick: int) -> tuple[str, float]:
    if pick_type == "moneyline":
        if "HOME" in pick_value:
            won = home_score > away_score
        else:
            won = away_score > home_score
        if home_score == away_score:
            return "push", 0.0
    elif pick_type == "spread":
        match = re.search(r"([+-]?\d+\.?\d*)", pick_value)
        spread = float(match.group(1)) if match else 0.0
        if "HOME" in pick_value:
            margin = home_score - away_score + spread
        else:
            margin = away_score - home_score + spread
        if margin == 0:
            return "push", 0.0
        won = margin > 0
    elif pick_type == "over_under":
        match = re.search(r"(\d+\.?\d*)", pick_value)
        total_line = float(match.group(1)) if match else 0.0
        actual_total = home_score + away_score
        if actual_total == total_line:
            return "push", 0.0
        if "Over" in pick_value:
            won = actual_total > total_line
        else:
            won = actual_total < total_line
    else:
        return "loss", -1.0
    if won:
        return "win", calculate_payout(odds_at_pick)
    else:
        return "loss", -1.0


def grade_prop_pick(pick_value: str, market: str, player_stat) -> tuple[str, float] | None:
    """Grade a prop pick against actual player stats.

    pick_value format: "PlayerName Over/Under 25.5 MarketLabel"
    Returns (result, payout_ratio) or None if stats not available.
    """
    if player_stat is None:
        return None

    stat_fields = MARKET_STAT_MAP.get(market)
    if not stat_fields:
        return None

    # Sum the relevant stat fields from the player's game log
    actual = 0.0
    for field in stat_fields:
        val = getattr(player_stat, field, None)
        if val is not None:
            actual += val
        else:
            return None  # Missing stat data

    # Parse the line from pick_value (e.g., "LeBron James Over 25.5 Points")
    line_match = re.search(r"(Over|Under)\s+(\d+\.?\d*)", pick_value)
    if not line_match:
        return None

    direction = line_match.group(1)
    line = float(line_match.group(2))

    # Special case: anytime TD is binary (scored >= 1 TD)
    if market == "player_anytime_td":
        if actual >= 1:
            return "win", 1.0
        else:
            return "loss", -1.0

    if actual == line:
        return "push", 0.0
    if direction == "Over":
        won = actual > line
    else:
        won = actual < line

    return ("win", 1.0) if won else ("loss", -1.0)


def capture_closing_odds(session, pick_result, game_id: int, pick_type: str, pick_value: str):
    """Store closing odds on a PickResult from the most recent odds snapshot.

    Called during grading when a game reaches 'final' status.
    The most recent pre-game odds snapshot is the closing line.
    """
    from backend.models import Odds
    closing = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp.desc())
        .first()
    )
    if not closing:
        return

    if pick_type == "moneyline":
        if "HOME" in pick_value:
            pick_result.odds_at_close = closing.moneyline_home
        else:
            pick_result.odds_at_close = closing.moneyline_away
    elif pick_type == "spread":
        pick_result.odds_at_close = -110
    elif pick_type == "over_under":
        pick_result.odds_at_close = -110
