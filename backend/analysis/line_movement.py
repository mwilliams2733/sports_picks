"""Detect sharp money signals from odds movement patterns.

Reads the append-only `line_snapshots` series, one bookmaker at a time.

What this used to do
--------------------
Both functions queried `Odds` for a game and ordered by timestamp, calling
the rows "snapshots". `Odds` holds one row per (game, BOOKMAKER) and is
upserted in place, so that ordering ordered BOOKS, not observations:
``snapshots[0]`` was DraftKings and ``snapshots[-1]`` was BetRivers, and the
reported move was the disagreement between two books at a single instant.

Confirmed in production on 2026-09-22 -- game 1018 carried 15 Odds rows, one
per bookmaker, spreads from -7.0 to -2.5, and this module would have called
that a 4.5 point line move on a line that may never have moved.

``bookmaker`` is therefore required rather than optional. Movement across
books is the bug, so it is not expressible.
"""
from sqlalchemy.orm import Session

from backend.analysis.line_snapshots import line_history


def analyze_line_movement(session: Session, game_id: int, *,
                          bookmaker: str) -> dict | None:
    """Analyse one book's price series for a game to detect sharp action.

    Returns movement metrics, or None when that book has fewer than two
    observations on record -- one price is a quote, not a movement.
    """
    snapshots = line_history(session, game_id, bookmaker=bookmaker)

    if len(snapshots) < 2:
        return None

    first = snapshots[0]
    last = snapshots[-1]

    result = {"game_id": game_id, "bookmaker": bookmaker,
              "snapshots": len(snapshots)}

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


def get_steam_moves(session: Session, game_id: int, *, bookmaker: str,
                    threshold: float = 0.5) -> list[dict]:
    """Sudden large moves between consecutive observations from one book."""
    snapshots = line_history(session, game_id, bookmaker=bookmaker)

    steam_moves = []
    for i in range(1, len(snapshots)):
        prev, curr = snapshots[i - 1], snapshots[i]
        if prev.spread_home is not None and curr.spread_home is not None:
            move = abs(curr.spread_home - prev.spread_home)
            if move >= threshold:
                steam_moves.append({
                    "timestamp": str(curr.captured_at),
                    "bookmaker": bookmaker,
                    "spread_before": prev.spread_home,
                    "spread_after": curr.spread_home,
                    "move": curr.spread_home - prev.spread_home,
                })

    return steam_moves
