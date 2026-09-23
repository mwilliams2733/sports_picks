"""Read and write the append-only price history in ``line_snapshots``.

Why the table exists
--------------------
``Odds`` holds one row per (game, bookmaker) and ``_store_odds`` overwrites
it in place on every scout run. Every price this project has ever seen
except the latest is therefore gone, which blocks three separate things:

* **the opening line.** Beating the close is the hardest problem in the
  market; beating the opener is a different and much softer one. Neither can
  be attempted without knowing what the opener was.
* **line movement.** ``analyze_line_movement`` ordered ``Odds`` rows by
  timestamp and called them snapshots -- but ordering those rows orders
  BOOKMAKERS, not observations, so it was measuring the disagreement between
  DraftKings and FanDuel at one instant and reporting it as a line moving.
* **closing line value.** CLV is the only edge measurement that resolves
  before the game does, and it needs a pre-game price and a closing price
  from the same book.

This module does not change ``Odds``. That row stays the current-price cache
everything already reads; the snapshot series is recorded beside it.

Append on change
----------------
``record_snapshot`` appends only when a price DIFFERS from the latest row for
the same (game, bookmaker). An unchanged re-observation extends
``last_seen_at`` instead. So a line that held all morning is one row saying
so, not six identical ones -- and is still distinguishable from a line
nobody watched, which a plain dedupe would lose.

A price going absent is a change. A book pulling its total is a market
event, and recording it as "no new observation" would silently stitch the
pre- and post-pull quotes into one continuous run.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models import Game, LineSnapshot
from backend.time_utils import game_start_utc

logger = logging.getLogger(__name__)

#: The price columns that make up one observation. A field missing here is a
#: field whose movement the history silently loses, so it is derived from the
#: model rather than retyped: every column except the identity and the two
#: timestamps is a price.
PRICE_FIELDS: tuple[str, ...] = tuple(
    c.name for c in LineSnapshot.__table__.columns
    if c.name not in ("id", "game_id", "bookmaker", "captured_at",
                      "last_seen_at")
)


def _values(prices: Mapping) -> dict:
    """Pull just the price fields out of whatever mapping a caller passes.

    The pipeline hands over its bookmaker dict whole, which also carries
    ``key`` and other feed metadata; taking only `PRICE_FIELDS` means a new
    key in the feed cannot land in a price column.
    """
    return {f: prices.get(f) for f in PRICE_FIELDS}


def latest_snapshot(session: Session, game_id: int,
                    bookmaker: str) -> LineSnapshot | None:
    """The most recent observation for one book on one game."""
    return (session.query(LineSnapshot)
            .filter(LineSnapshot.game_id == game_id,
                    LineSnapshot.bookmaker == bookmaker)
            .order_by(LineSnapshot.captured_at.desc(), LineSnapshot.id.desc())
            .first())


def record_snapshot(session: Session, game_id: int, bookmaker: str,
                    prices: Mapping, *, now: datetime | None = None) -> bool:
    """Record an observed price. Returns True if a new row was appended.

    False means the price was unchanged since the last look, in which case
    ``last_seen_at`` on the existing row moves forward instead. The caller is
    not being told nothing happened -- it is being told the line held.

    Comparison is against the LATEST snapshot only, so a line that moves
    -3.5 -> -4.5 -> -3.5 correctly records three observations rather than
    collapsing the return into the original row.
    """
    now = now or datetime.now(tz=timezone.utc)
    values = _values(prices)
    previous = latest_snapshot(session, game_id, bookmaker)

    if previous is not None and all(
            getattr(previous, field) == values[field]
            or (getattr(previous, field) is None and values[field] is None)
            for field in PRICE_FIELDS):
        previous.last_seen_at = now
        return False

    session.add(LineSnapshot(game_id=game_id, bookmaker=bookmaker,
                             captured_at=now, last_seen_at=now, **values))
    session.flush()
    return True


def line_history(session: Session, game_id: int, *,
                 bookmaker: str | None = None) -> list[LineSnapshot]:
    """Every observation for a game, oldest first.

    Without ``bookmaker`` this interleaves books, which is almost never what
    a movement calculation wants -- see the module docstring.
    """
    q = session.query(LineSnapshot).filter(LineSnapshot.game_id == game_id)
    if bookmaker is not None:
        q = q.filter(LineSnapshot.bookmaker == bookmaker)
    return q.order_by(LineSnapshot.captured_at, LineSnapshot.id).all()


def opening_line(session: Session, game_id: int, *,
                 bookmaker: str | None = None) -> LineSnapshot | None:
    """The earliest price on record.

    This is the opening line **as observed**, not as the market set it: a
    game first seen on Thursday has no record of Sunday-night's opener. The
    gap between ``captured_at`` and kickoff is how a caller tells an early
    look from a late one, and any study of opening-line value has to filter
    on it rather than assume.
    """
    history = line_history(session, game_id, bookmaker=bookmaker)
    return history[0] if history else None


def closing_line(session: Session, game_id: int, *,
                 bookmaker: str | None = None) -> LineSnapshot | None:
    """The last price observed BEFORE kickoff.

    Not simply the last price. Once a game starts the odds feed serves
    in-play numbers, and a 7-point pre-game favourite leading by 14 prices
    nothing anybody could have bet. Returning that as "the close" would
    quietly poison every CLV measurement built on it -- so a game whose only
    snapshots are in-play returns None. An absent close is honest; a
    fabricated one is not.

    A game with no ``start_time`` has no cutoff to apply, so the latest
    snapshot is used: unknown is not past, the convention ``skip_started``
    already follows.
    """
    history = line_history(session, game_id, bookmaker=bookmaker)
    if not history:
        return None

    game = session.get(Game, game_id)
    start = game_start_utc(game) if game is not None else None
    if start is None:
        return history[-1]

    pre_game = [s for s in history
                if s.captured_at.replace(tzinfo=timezone.utc) < start]
    return pre_game[-1] if pre_game else None
