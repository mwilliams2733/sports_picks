"""Record the append-only price history of player props (`prop_snapshots`).

Why it exists
-------------
`PlayerProp` holds one row per (game, book, market, player, outcome) and
`full_pipeline._store_props` overwrites it on every fetch, so every earlier
prop price is gone. The NBA late scratch question -- do books leave
teammates' lines stale for a while after a star is ruled out? -- needs the
price before the news and the price after. Since 2026-10-07 each NBA window
also re-fetches props 45 minutes before tip (`scheduler._run_late_props`),
so the before/after pairs exist; this keeps them.

Rules (the same as `line_snapshots`, per prop):

* **Append on change.** A new row only when the line or odds differ from the
  latest row for that key; an unchanged look extends `last_seen_at`.
* **A pull is a change.** If a book quoted a market in this fetch but no
  longer quotes a player it quoted before, a row with `odds` NULL is
  appended. Scoped to (book, market) pairs present in THIS fetch, so a
  partial or empty response cannot fake a wave of pulls.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models import PropSnapshot

KEY = ("bookmaker", "market", "player_name", "outcome")


def _key(row) -> tuple:
    get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k))
    return tuple(get(k) for k in KEY)


def latest_by_key(session: Session, game_id: int) -> dict[tuple, PropSnapshot]:
    """The latest snapshot per prop key for one game, in one query."""
    latest: dict[tuple, PropSnapshot] = {}
    for row in (session.query(PropSnapshot).filter(PropSnapshot.game_id == game_id)
                .order_by(PropSnapshot.captured_at, PropSnapshot.id)):
        latest[_key(row)] = row
    return latest


def record_prop_fetch(session: Session, game_id: int, props: list[dict], *,
                      now: datetime | None = None) -> dict:
    """Record one fetch's props for a game. Returns counts:
    appended (price changed or new), held (unchanged), pulled."""
    now = now or datetime.now(tz=timezone.utc)
    latest = latest_by_key(session, game_id)
    counts = {"appended": 0, "held": 0, "pulled": 0}
    seen = set()
    for p in props:
        key = _key(p)
        if key in seen:
            continue
        seen.add(key)
        prev = latest.get(key)
        if prev is not None and prev.odds == p["odds"] and prev.line == p.get("line"):
            prev.last_seen_at = now
            counts["held"] += 1
            continue
        session.add(PropSnapshot(game_id=game_id, captured_at=now, last_seen_at=now,
                                 line=p.get("line"), odds=p["odds"],
                                 **dict(zip(KEY, key))))
        counts["appended"] += 1
    quoted = {(k[0], k[1]) for k in seen}
    for key, prev in latest.items():
        if key in seen or prev.odds is None or (key[0], key[1]) not in quoted:
            continue
        session.add(PropSnapshot(game_id=game_id, captured_at=now, last_seen_at=now,
                                 line=None, odds=None, **dict(zip(KEY, key))))
        counts["pulled"] += 1
    session.flush()
    return counts


def prop_history(session: Session, game_id: int, player: str, market: str, *,
                 bookmaker: str | None = None) -> list[PropSnapshot]:
    """Every observation of one player's market on one game, oldest first."""
    q = session.query(PropSnapshot).filter(PropSnapshot.game_id == game_id,
                                           PropSnapshot.player_name == player,
                                           PropSnapshot.market == market)
    if bookmaker is not None:
        q = q.filter(PropSnapshot.bookmaker == bookmaker)
    return q.order_by(PropSnapshot.captured_at, PropSnapshot.id).all()
