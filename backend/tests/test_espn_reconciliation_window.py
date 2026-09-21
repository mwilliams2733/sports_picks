"""Reconciliation must not cancel a game it merely failed to look up.

`_reconcile_against_espn` marks a pending row canceled when ESPN's list for
the target date lacks its team pair. On 2026-09-20 that had wrongly
canceled 15 played games holding 18 ungraded picks (see
`test_restore_miscanceled_games.py`). Two independent reasons the pair
goes missing, neither of which means the game did not happen:

**1. The slate was incomplete.** `fetch_scoreboard` sent only ``dates``.
ESPN then answers with a featured subset: 2 ncaab events for
2026-03-15, a conference championship Sunday. `groups=50` (Division I)
returns the rest. For football `groups=80` is FBS -- and note `groups=50`
there returns 6 events for a full September Saturday, which is how the
first audit of this bug reached the wrong answer.

**2. The date is off by one, on EITHER side.** ESPN timestamps in UTC and
files by Eastern date, and our own rows drift too: `VAN VS NEB` is stored
here as 2026-03-22 while its true Eastern date is 03-21, and six games we
date 03-23 are ESPN's 03-22. So it is not enough to fetch the neighbouring
stamps and keep filtering on ``et_date == target_date`` -- the DB side is
wrong as often as the ESPN side. A pair seen anywhere in the +/-1 day
window counts as confirmation.

The asymmetry is deliberate: failing to cancel a phantom leaves a row that
never grades, while canceling a real game destroys a result and books its
picks as pushes. Two fighters -- or two teams -- meeting twice inside three
days is rare enough to accept against that.
"""
from datetime import date

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import _store_games

TARGET = date(2026, 5, 1)
ON_TARGET = "2026-05-01T22:00:00Z"      # et_date 2026-05-01
DAY_BEFORE = "2026-04-30T23:00:00Z"     # et_date 2026-04-30
DAY_AFTER = "2026-05-03T00:30:00Z"      # et_date 2026-05-02
TWO_DAYS_AFTER = "2026-05-04T00:30:00Z"  # et_date 2026-05-03


def _event(home, away, when=ON_TARGET, status="scheduled"):
    return {"home_team": home, "home_team_name": home,
            "away_team": away, "away_team_name": away,
            "date": when, "status": status,
            "home_score": None, "away_score": None}


@pytest.fixture
def session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="Nebraska", abbreviation="NEB", sport="ncaab"),
        Team(id=2, name="Vanderbilt", abbreviation="VAN", sport="ncaab"),
        Team(id=3, name="Detroit", abbreviation="DET", sport="ncaab"),
        Team(id=4, name="Orlando", abbreviation="ORL", sport="ncaab"),
    ])
    s.commit()
    yield s
    s.close()


def _game(session, status="scheduled", gid=100):
    g = Game(id=gid, sport="ncaab", season="2026", date=TARGET,
             home_team_id=1, away_team_id=2, status=status)
    session.add(g)
    session.commit()
    return g


def test_a_pair_espn_files_a_day_EARLY_is_not_canceled(session):
    """`VAN VS NEB` is stored here as 03-22; ESPN's Eastern date is 03-21."""
    _game(session)

    _store_games(session, "ncaab", TARGET, [
        _event("DET", "ORL", ON_TARGET),
        _event("NEB", "VAN", DAY_BEFORE),
    ])

    assert session.get(Game, 100).status == "scheduled"


def test_a_pair_espn_files_a_day_LATE_is_not_canceled(session):
    """Six games we date 03-23 are ESPN's 03-22. The drift runs both ways,
    so a one-sided window would still cancel half of them."""
    _game(session)

    _store_games(session, "ncaab", TARGET, [
        _event("DET", "ORL", ON_TARGET),
        _event("NEB", "VAN", DAY_AFTER),
    ])

    assert session.get(Game, 100).status == "scheduled"


def test_a_pair_absent_from_the_whole_window_is_still_canceled(session):
    """The phantom case the reconciler exists for: the Odds API posts lines
    for a playoff game a series ended short of. Widening the window must not
    disarm it."""
    _game(session)

    _store_games(session, "ncaab", TARGET, [_event("DET", "ORL", ON_TARGET)])

    assert session.get(Game, 100).status == "canceled"


def test_a_pair_two_days_away_does_not_rescue(session):
    """The window is +/-1 day, matching `espn_box_score.resolve_espn_event`.
    An unbounded search would treat any future rematch as confirmation."""
    _game(session)

    _store_games(session, "ncaab", TARGET, [
        _event("DET", "ORL", ON_TARGET),
        _event("NEB", "VAN", TWO_DAYS_AFTER),
    ])

    assert session.get(Game, 100).status == "canceled"


def test_a_canceled_row_seen_at_a_neighbouring_date_is_restored(session):
    """The repair path: a game wrongly canceled by the old logic comes back
    on its own once the pair is seen anywhere in the window."""
    _game(session, status="canceled")

    _store_games(session, "ncaab", TARGET, [
        _event("DET", "ORL", ON_TARGET),
        _event("NEB", "VAN", DAY_BEFORE),
    ])

    assert session.get(Game, 100).status == "scheduled"


def test_a_final_row_is_still_never_touched(session):
    """A restored game is final, and must stay final however the window
    resolves -- otherwise the next run undoes the repair."""
    g = _game(session, status="final")
    g.home_score, g.away_score = 74, 72
    session.commit()

    _store_games(session, "ncaab", TARGET, [_event("DET", "ORL", ON_TARGET)])

    assert session.get(Game, 100).status == "final"


def test_no_reconciliation_when_espn_has_nothing_on_the_target_date(session):
    """Neighbours alone cannot prove the target date was covered. Without an
    event ON the date, an outage is indistinguishable from an off-day, and
    canceling the whole slate is the worst possible reading."""
    _game(session)

    _store_games(session, "ncaab", TARGET, [_event("DET", "ORL", DAY_BEFORE)])

    assert session.get(Game, 100).status == "scheduled"
