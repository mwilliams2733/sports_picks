"""An in-play price must not overwrite the last takeable one.

The Odds API `/odds` endpoint returns every event for a sport, including
games already underway. `fetch_and_store_odds` took the whole response and
`_store_odds` wrote all of it, so a fetch aimed at the 01:40 games also
re-priced the 22:40 games that had been playing for three hours -- replacing
each one's last pre-game quote with an in-play number nobody could have bet.

Measured 2026-09-23 on genuinely observed rows (not the backfill, whose
timestamps are last-write times and cannot answer this): one fetch touched
208 mlb bookmaker rows, 80 of them (38%) on games already started, across 10
games.

`fetch_and_store_props` was already scoped by `window_game_ids`. Odds never
was. The guard here is on game state rather than window membership, because
the invariant is "do not overwrite a takeable price with an untakeable one"
-- which is equally true for `fetch_odds_now` and `fetch_windowless_odds`,
neither of which has a window.

This is the same rule `generate_and_store_picks` already follows, and it
uses the same `skip_started` name and the same escape hatch for backfills.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, LineSnapshot, Odds, Team
from backend.pipeline.full_pipeline import _store_odds

def _hours(n):
    """A start time n hours from now, so these tests do not rot."""
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=n)).replace(tzinfo=None)


#: The event's commence_time MUST agree with the game's start_time, or
#: `_find_game_by_teams` never matches and every test below passes because
#: nothing was found rather than because something was skipped.
UPCOMING = _hours(3)
UNDERWAY = _hours(-3)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "s.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="Buffalo Bills", abbreviation="BUF", sport="nfl"),
               Team(id=2, name="Miami Dolphins", abbreviation="MIA", sport="nfl")])
    s.flush()
    s.add_all([
        Game(id=10, sport="nfl", season="2026-27", date=UPCOMING.date(),
             status="scheduled", home_team_id=1, away_team_id=2,
             start_time=UPCOMING),
        Game(id=11, sport="nfl", season="2026-27", date=UNDERWAY.date(),
             status="scheduled", home_team_id=2, away_team_id=1,
             start_time=UNDERWAY),
    ])
    s.commit()
    return s


def _event(home, away, spread=-3.5, *, when=None):
    return {"home_team": home, "away_team": away,
            "commence_time": (when or UPCOMING).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bookmakers": [{"key": "draftkings", "moneyline_home": -150,
                            "moneyline_away": 130, "spread_home": spread,
                            "spread_away": -spread, "over_under": 44.5,
                            "spread_home_price": -110, "spread_away_price": -110,
                            "over_price": -110, "under_price": -110}]}


def _upcoming(**kw):
    return _event("Buffalo Bills", "Miami Dolphins", when=UPCOMING, **kw)


def _underway(**kw):
    return _event("Miami Dolphins", "Buffalo Bills", when=UNDERWAY, **kw)


def test_an_upcoming_game_is_priced(session):
    assert _store_odds(session, "nfl", [_upcoming()]) == 1
    assert session.query(Odds).count() == 1


def test_a_game_already_underway_is_not_priced(session):
    """The regression."""
    assert _store_odds(session, "nfl", [_underway()]) == 0
    assert session.query(Odds).count() == 0


def test_a_started_games_existing_price_is_left_untouched(session):
    """The harm was not a missing row, it was a REPLACED one: the last
    pre-game quote overwritten by an in-play number."""
    session.add(Odds(game_id=11, bookmaker="draftkings", spread_home=-3.5,
                     moneyline_home=-150))
    session.commit()

    _store_odds(session, "nfl", [_underway(spread=-14.5)])

    assert session.query(Odds).one().spread_home == -3.5, \
        "an in-play price overwrote the last takeable one"


def test_a_started_game_records_no_snapshot_either(session):
    """Otherwise the series fills with in-play observations and
    `closing_line` has to discard them all."""
    _store_odds(session, "nfl", [_underway()])

    assert session.query(LineSnapshot).count() == 0


def test_a_game_with_no_start_time_is_still_priced(session):
    """Unknown is not past -- the convention `skip_started` already uses.
    Boxing and mma fixtures often have no start_time at all."""
    session.query(Game).filter(Game.id == 10).update({"start_time": None})
    session.commit()

    assert _store_odds(session, "nfl", [_upcoming()]) == 1


def test_one_started_game_does_not_stop_the_rest_of_the_slate(session):
    """A whole slate arrives in one response. Bailing on the first started
    game would drop every later one with it."""
    stored = _store_odds(session, "nfl",
                         [_underway(), _upcoming()])

    assert stored == 1
    assert session.query(Odds).one().game_id == 10


def test_a_backfill_can_opt_out(session):
    """Backtests deliberately price games that are long over, matching
    `generate_and_store_picks(skip_started=False)`."""
    assert _store_odds(session, "nfl", [_underway()],
                       skip_started=False) == 1


def test_the_skip_is_logged_with_a_count(session, caplog):
    """A price that vanishes with no record is indistinguishable from an
    API that returned nothing -- the failure mode this repo keeps shipping.
    A count, not a line per game, so the log stays readable on a full slate.
    """
    import backend.pipeline.full_pipeline as fp

    with caplog.at_level("INFO", logger=fp.__name__):
        _store_odds(session, "nfl", [_underway()])

    assert any("already underway" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_nothing_is_logged_when_no_game_was_skipped(session):
    """A warning that always fires stops being read."""
    import logging

    import backend.pipeline.full_pipeline as fp
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger(fp.__name__)
    logger.addHandler(handler)
    try:
        _store_odds(session, "nfl", [_upcoming()])
    finally:
        logger.removeHandler(handler)

    assert not [r for r in records if "already underway" in r.getMessage()]
