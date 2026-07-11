import logging
import re
from backend.analysis.odds_utils import calculate_payout, InvalidOddsError

logger = logging.getLogger(__name__)

# Lines are parsed from strings like "-3.5"; float arithmetic on top of that
# (e.g. margin = home_score - away_score + spread) can land a hair off an
# exact push, so pushes are detected within this tolerance rather than `==`.
_PUSH_EPSILON = 0.001

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
        if abs(margin) < _PUSH_EPSILON:
            return "push", 0.0
        won = margin > 0
    elif pick_type == "over_under":
        match = re.search(r"(\d+\.?\d*)", pick_value)
        total_line = float(match.group(1)) if match else 0.0
        actual_total = home_score + away_score
        if abs(actual_total - total_line) < _PUSH_EPSILON:
            return "push", 0.0
        if "Over" in pick_value:
            won = actual_total > total_line
        else:
            won = actual_total < total_line
    else:
        return "loss", -1.0
    if won:
        try:
            return "win", calculate_payout(odds_at_pick)
        except InvalidOddsError:
            logger.warning("Invalid odds_at_pick=%r for %s pick %r; grading win with 0.0 payout",
                            odds_at_pick, pick_type, pick_value)
            return "win", 0.0
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

    if abs(actual - line) < _PUSH_EPSILON:
        return "push", 0.0
    if direction == "Over":
        won = actual > line
    else:
        won = actual < line

    return ("win", 1.0) if won else ("loss", -1.0)


def capture_closing_odds(session, pick_result, game_id: int, pick_type: str, pick_value: str, odds_at_pick: int | None = None):
    """Store closing odds (and, for spread/total, the closing line) on a PickResult.

    Called during grading when a game reaches 'final' status. The most recent
    pre-game odds snapshot is treated as the closing line.

    For moneyline bets the price moves materially, so odds_at_close stores the
    closing moneyline price. For spread/total bets the price (juice) is rarely
    stored historically and barely moves; the meaningful CLV is in the line
    number, which is stored in line_at_close. odds_at_close defaults to
    odds_at_pick for those bet types so price-CLV becomes a no-op rather than
    fabricated -110.
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
        if "HOME" in pick_value:
            pick_result.line_at_close = closing.spread_home
        else:
            pick_result.line_at_close = closing.spread_away
        pick_result.odds_at_close = odds_at_pick
    elif pick_type == "over_under":
        pick_result.line_at_close = closing.over_under
        pick_result.odds_at_close = odds_at_pick


def _apply_combat_elo_update(session, game) -> None:
    """K=24 binary-outcome Elo update for combat sports.

    home_score=1, away_score=0 means home won; reversed means away won.
    Both = 1 indicates a draw (outcome 0.5 for both).
    """
    from backend.analysis.elo import get_k_factor
    from backend.models import EloRating
    K = get_k_factor(game.sport)

    home_elo_row = (session.query(EloRating)
                    .filter(EloRating.team_id == game.home_team_id, EloRating.sport == game.sport)
                    .first())
    away_elo_row = (session.query(EloRating)
                    .filter(EloRating.team_id == game.away_team_id, EloRating.sport == game.sport)
                    .first())
    if home_elo_row is None or away_elo_row is None:
        return  # missing Elo rows; skip rather than crash

    h, a = home_elo_row.rating, away_elo_row.rating
    expected_home = 1 / (1 + 10 ** ((a - h) / 400))
    if game.home_score == game.away_score:
        actual_home = 0.5
    elif (game.home_score or 0) > (game.away_score or 0):
        actual_home = 1.0
    else:
        actual_home = 0.0
    delta = K * (actual_home - expected_home)
    home_elo_row.rating += delta
    away_elo_row.rating -= delta


def grade_completed_games(session) -> None:
    """Apply post-game updates for all finalized games.

    Currently handles combat-sports Elo updates only. Team-sport Elo is
    managed by the backtesting/historical path and is not touched here.

    NOTE: This function is not yet wired into the live scheduler. Combat-8
    (UFCStats event ingest) should call this after committing fight outcomes,
    or a follow-up task should add a post-grading step in `scheduler._run_window`.
    Until then, fighter Elo will remain at seed values in production.
    """
    from backend.models import Game
    final_games = (
        session.query(Game)
        .filter(Game.status == "final",
                Game.home_score.isnot(None),
                Game.away_score.isnot(None))
        .all()
    )
    for game in final_games:
        if game.sport in ("mma", "boxing"):
            _apply_combat_elo_update(session, game)
    session.commit()
