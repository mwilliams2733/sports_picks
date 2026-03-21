"""Detect sharp money signals from odds movement patterns."""

from backend.models import Odds
from sqlalchemy.orm import Session


def analyze_line_movement(session: Session, game_id: int) -> dict | None:
    """Analyze odds snapshots for a game to detect sharp action.

    Returns dict with movement metrics or None if insufficient data.
    """
    snapshots = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp)
        .all()
    )

    if len(snapshots) < 2:
        return None

    first = snapshots[0]
    last = snapshots[-1]

    result = {"game_id": game_id, "snapshots": len(snapshots)}

    if first.moneyline_home is not None and last.moneyline_home is not None:
        result["ml_move_home"] = last.moneyline_home - first.moneyline_home
        result["ml_move_away"] = (last.moneyline_away or 0) - (first.moneyline_away or 0)

    if first.spread_home is not None and last.spread_home is not None:
        result["spread_move"] = last.spread_home - first.spread_home

    if first.over_under is not None and last.over_under is not None:
        result["ou_move"] = last.over_under - first.over_under

    if "spread_move" in result and "ml_move_home" in result:
        spread_moved_toward_home = result["spread_move"] < -0.5
        ml_moved_toward_home = result["ml_move_home"] < 0
        result["reverse_line_movement"] = (
            (spread_moved_toward_home and not ml_moved_toward_home) or
            (not spread_moved_toward_home and ml_moved_toward_home)
        )
        if result.get("reverse_line_movement"):
            result["sharp_side"] = "home" if ml_moved_toward_home else "away"
    else:
        result["reverse_line_movement"] = False

    return result


def get_steam_moves(session: Session, game_id: int, threshold: float = 0.5) -> list[dict]:
    """Detect sudden large line movements (steam moves) between consecutive snapshots."""
    snapshots = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp)
        .all()
    )

    steam_moves = []
    for i in range(1, len(snapshots)):
        prev, curr = snapshots[i - 1], snapshots[i]
        if prev.spread_home is not None and curr.spread_home is not None:
            move = abs(curr.spread_home - prev.spread_home)
            if move >= threshold:
                steam_moves.append({
                    "timestamp": str(curr.timestamp),
                    "spread_before": prev.spread_home,
                    "spread_after": curr.spread_home,
                    "move": curr.spread_home - prev.spread_home,
                })

    return steam_moves
