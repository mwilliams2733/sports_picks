"""Append a `PickVersion` row whenever a pick's tracked fields change.

Same append-on-change idiom `backend/analysis/line_snapshots.py` uses for
prices: a row is written only when the pick differs from the latest stored
version, so a pick refreshed with identical values (a scout run that changed
nothing) writes nothing. `PickModel` itself is never touched here -- this
module only appends to `pick_versions`; the picks table's overwrite-in-place
behaviour, and everything that reads it (email, the frontend, grading), is
unchanged.

One helper for both write paths (game picks and props) so the comparison and
version-numbering logic exists exactly once.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import PickModel, PickVersion

logger = logging.getLogger(__name__)

#: The fields a version tracks. Mirrors `PickModel`'s own columns of the same
#: name; `strategy_id` is deliberately excluded -- it is not refreshed by
#: either write path (see the "known traps" note in docs/data-dictionary.md)
#: and tracking a field nothing ever changes would only ever write version 1.
TRACKED_FIELDS: tuple[str, ...] = (
    "pick_value", "confidence", "edge_pct", "odds_at_pick", "model_prob",
    "suggested_unit_size", "rationale_json", "withdrawn",
)


def _equal(a, b) -> bool:
    """Exact comparison, floats included: values are stored as computed, not
    as rounded display figures, so an exact match is the right bar for
    "nothing changed" -- same as `line_snapshots.record_snapshot`. None ==
    None is equal; None never equals a real value."""
    return a == b


def _changed(latest: PickVersion | None, pick: PickModel) -> bool:
    if latest is None:
        return True
    return any(
        not _equal(getattr(latest, field), getattr(pick, field))
        for field in TRACKED_FIELDS
    )


def _next_version(session: Session, pick_id: int) -> tuple[int, PickVersion | None]:
    """The version number `pick_id`'s next row should take, and the latest
    row it was computed from (for `_changed`'s comparison). Split out from
    `record_pick_version` so a test can force a stale/colliding number
    without faking real thread concurrency."""
    latest = (session.query(PickVersion)
              .filter(PickVersion.pick_id == pick_id)
              .order_by(PickVersion.version.desc())
              .first())
    next_version = 1 if latest is None else latest.version + 1
    return next_version, latest


def record_pick_version(session: Session, pick: PickModel,
                        source: str) -> PickVersion | None:
    """Append a new `PickVersion` for `pick` if its tracked fields changed.

    Returns the new row, or `None` if the latest stored version already
    matches (or `source='insert'` always writes version 1 -- there is no
    prior version to match against), or if a `(pick_id, version)` collision
    was caught (see below).

    `pick` must have an id already; if it does not (a freshly-constructed,
    unflushed row), this flushes the session first so the FK is available.

    `recorded_at` is now (UTC), except for `source='backfill'`: there the
    caller is reconstructing a version for a pick this table never saw, and
    the pick's own `created_at` is the best available estimate of when that
    state existed -- "now" would be a fabricated observation time.

    The insert runs inside `session.begin_nested()` (a SAVEPOINT), and an
    `IntegrityError` on the `uq_pick_versions_pick_version` constraint is
    caught, logged, and swallowed rather than left to propagate.
    `BackgroundScheduler` runs window jobs from a thread pool, and
    `generate_and_store_picks` can overlap across threads; two overlapping
    runs can each compute the same next-version number for the same pick
    from a `_next_version` read taken before either has written. Before
    this table existed, overlapping runs just silently overwrote each
    other's refresh -- a lost VERSION here must not become a lost RUN: an
    `IntegrityError` bubbling out of this call would abort the caller's
    `session.commit()` for the whole window (game picks AND, on the same
    session, that window's props). The version this call would have
    written is lost, but the refreshed `PickModel` row -- already flushed
    by the `_next_version` lookup's autoflush, before the nested block ever
    opens -- survives, and the run continues.
    """
    if pick.id is None:
        session.flush()

    next_version, latest = _next_version(session, pick.id)
    if not _changed(latest, pick):
        return None

    recorded_at = (pick.created_at if source == "backfill"
                   else datetime.now(tz=timezone.utc))
    row = PickVersion(
        pick_id=pick.id, version=next_version, recorded_at=recorded_at,
        source=source,
        pick_value=pick.pick_value, confidence=pick.confidence,
        edge_pct=pick.edge_pct, odds_at_pick=pick.odds_at_pick,
        model_prob=pick.model_prob,
        suggested_unit_size=pick.suggested_unit_size,
        rationale_json=pick.rationale_json,
        withdrawn=pick.withdrawn,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        logger.warning(
            "pick_versions collision for pick_id=%s (version=%s, source=%s) "
            "-- another writer already recorded this version; skipping",
            pick.id, next_version, source)
        return None
    return row
