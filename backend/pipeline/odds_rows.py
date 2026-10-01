"""One `Odds` row per (game, bookmaker), and how to keep it that way.

`Odds` is the CURRENT price, upserted per book: the collector looks up the
row for (game, bookmaker) and updates it. Nothing enforced that there was
only one, and the two game-merge scripts reparented a dropped game's rows
onto the survivor without asking whether the survivor already had that
book. When it did, the pair lived on side by side: the collector's
``.first()`` kept updating one, the other froze, and every consumer that
consensuses a game's rows (pricing, the pick generator, /games) counted
that book twice while the stale twin was still inside its freshness window.

The fix is the rule, enforced by the ``uq_odds_game_bookmaker`` unique
index (`backend.database.migrate_odds_one_row_per_book`), and these helpers
for the places that move or clean rows. Newest wins everywhere: it is the
quote the collector would have written last, and the older one is a price
nobody is offering any more.
"""
from collections import defaultdict

from backend.models import Odds


def _newest_first(rows: list[Odds]) -> list[Odds]:
    # id breaks a timestamp tie deterministically: the later insert.
    return sorted(rows, key=lambda r: (r.timestamp, r.id), reverse=True)


def stale_duplicates(session) -> list[Odds]:
    """Every row that loses to a newer row for the same (game, bookmaker)."""
    groups: dict[tuple[int, str], list[Odds]] = defaultdict(list)
    for row in session.query(Odds).order_by(Odds.id):
        groups[(row.game_id, row.bookmaker)].append(row)
    doomed = []
    for rows in groups.values():
        if len(rows) > 1:
            doomed.extend(_newest_first(rows)[1:])
    return doomed


def drop_odds_collisions(session, from_game_id: int, to_game_id: int) -> int:
    """Clear the way for moving ``from_game_id``'s rows onto ``to_game_id``.

    For each bookmaker the two games carry between them, keeps only the
    newest row, whichever game it is on, and returns how many it deleted. The caller then
    reparents whatever is left on ``from_game_id`` exactly as before -- by
    then no book it moves is already on the survivor. Call it BEFORE the
    reparent: afterwards the unique index has already refused the move.
    """
    # Every row on either game, newest first, so the first row seen for a
    # book is its winner and every later one -- on either side, including a
    # pre-existing duplicate on one game alone -- is deleted.
    rows = (session.query(Odds)
            .filter(Odds.game_id.in_((from_game_id, to_game_id))).all())
    seen: set[str] = set()
    deleted = 0
    for row in _newest_first(rows):
        if row.bookmaker in seen:
            session.delete(row)
            deleted += 1
        else:
            seen.add(row.bookmaker)
    session.flush()
    return deleted
