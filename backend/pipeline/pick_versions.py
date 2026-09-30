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

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models import PickModel, PickVersion

#: The fields a version tracks. Mirrors `PickModel`'s own columns of the same
#: name; `strategy_id` is deliberately excluded -- it is not refreshed by
#: either write path (see the "known traps" note in docs/data-dictionary.md)
#: and tracking a field nothing ever changes would only ever write version 1.
TRACKED_FIELDS: tuple[str, ...] = (
    "pick_value", "confidence", "edge_pct", "odds_at_pick", "model_prob",
    "suggested_unit_size", "rationale_json",
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


def record_pick_version(session: Session, pick: PickModel,
                        source: str) -> PickVersion | None:
    """Append a new `PickVersion` for `pick` if its tracked fields changed.

    Returns the new row, or `None` if the latest stored version already
    matches (or `source='insert'` always writes version 1 -- there is no
    prior version to match against).

    `pick` must have an id already; if it does not (a freshly-constructed,
    unflushed row), this flushes the session first so the FK is available.

    `recorded_at` is now (UTC), except for `source='backfill'`: there the
    caller is reconstructing a version for a pick this table never saw, and
    the pick's own `created_at` is the best available estimate of when that
    state existed -- "now" would be a fabricated observation time.
    """
    if pick.id is None:
        session.flush()

    latest = (session.query(PickVersion)
              .filter(PickVersion.pick_id == pick.id)
              .order_by(PickVersion.version.desc())
              .first())
    if not _changed(latest, pick):
        return None

    next_version = 1 if latest is None else latest.version + 1
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
    )
    session.add(row)
    return row
