"""Guards for the season label.

Three sites wrote it, in three formats, from three different expressions:

    backtesting/historical.py   f"{y}-{str(y+1)[-2:]}"   -> "2025-26"
    pipeline/full_pipeline.py   f"{y}-{y+1}"             -> "2026-2027"
    pipeline/full_pipeline.py   f"{y}"                   -> "2026"

so the same season was recorded differently depending on which collector
created the row -- ESPN's path against the Odds API's. nba carried four
labels for two seasons. Nothing reads the column yet, which is the only
reason this was latent rather than a live bug.

The middle one was also wrong on its own terms: a January 2027 NFL game
belongs to the 2026 season, and `f"{y}-{y+1}"` on its date says "2027-2028".
"""
import datetime

import pytest

from backend.collectors.espn import season_year_of
from backend.config import season_label

SEASONS = {
    "nfl": {"start": "09-05", "end": "02-10"},
    "ncaaf": {"start": "08-24", "end": "01-20"},
    "nba": {"start": "10-22", "end": "06-20"},
    "mlb": {"start": "03-27", "end": "10-31"},
    "boxing": {"start": "01-01", "end": "12-31"},
}


def test_the_season_year_is_read_from_the_event():
    assert season_year_of({"season": {"year": 2026, "type": 2}}) == 2026


def test_an_absent_season_year_is_none_rather_than_a_guess():
    assert season_year_of({}) is None
    assert season_year_of({"season": {}}) is None
    assert season_year_of({"season": None}) is None
    assert season_year_of({"season": {"type": 2}}) is None


def test_a_non_numeric_season_year_is_refused():
    """`True` is an int in Python and would read as season 1."""
    assert season_year_of({"season": {"year": "2026"}}) is None
    assert season_year_of({"season": {"year": True}}) is None


def test_a_cross_year_season_is_named_for_the_year_it_started():
    assert season_label("nfl", datetime.date(2026, 9, 13), SEASONS) == "2026-27"


def test_a_game_after_new_year_keeps_its_season(): 
    """The defect in `f"{y}-{y+1}"`: January 2027 is the 2026 season."""
    assert season_label("nfl", datetime.date(2027, 1, 24), SEASONS) == "2026-27"
    assert season_label("ncaaf", datetime.date(2027, 1, 10), SEASONS) == "2026-27"
    assert season_label("nba", datetime.date(2027, 5, 2), SEASONS) == "2026-27"


def test_a_game_just_before_the_start_belongs_to_the_season_about_to_begin():
    """This assertion used to run the other way.

    "Is the date past the configured start" reads right at the TAIL of a
    season and wrong at its HEAD. It was asserted at nfl 09-04 -- one day
    out, where landing in the previous season looks like a rounding choice
    -- but the same rule sends an August preseason game a whole year back.
    The date belongs to the season it is NEAREST, which at 09-04 is the one
    starting the next day.
    """
    assert season_label("nfl", datetime.date(2026, 9, 4), SEASONS) == "2026-27"
    assert season_label("nfl", datetime.date(2026, 9, 5), SEASONS) == "2026-27"


def test_preseason_belongs_to_the_season_it_precedes():
    """The live shape of it: `seasons` is a coarse "is this sport active
    today" gate, accurate to a few days, and preseason sits in exactly the
    gap that inaccuracy opens. The scheduler cannot reach these dates --
    it filters on `is_sport_in_season` -- but `backfill_date_range` takes
    explicit dates and does not.
    """
    assert season_label("nfl", datetime.date(2026, 8, 15), SEASONS) == "2026-27"
    assert season_label("nba", datetime.date(2026, 10, 5), SEASONS) == "2026-27"
    assert season_label("ncaaf", datetime.date(2026, 8, 20), SEASONS) == "2026-27"


def test_the_tail_of_a_season_does_not_regress():
    """The half the old rule got right, and the reason it looked correct:
    an nba game in June is before October, so it lands in the season that
    began the previous October -- which is the right answer."""
    assert season_label("nba", datetime.date(2026, 6, 25), SEASONS) == "2025-26"
    assert season_label("ncaaf", datetime.date(2027, 1, 10), SEASONS) == "2026-27"


def test_espn_names_basketball_seasons_by_the_year_they_end():
    """Why ESPN's number is a candidate and never a label.

    ESPN calls the 2025-26 nba season `year: 2026` and the 2026 nfl season
    `year: 2026`. Formatting either directly would rename the 1,237 nba rows
    already stored as "2025-26" to "2026-27". Resolved against the window,
    both conventions land on the right season.
    """
    assert season_label("nba", datetime.date(2025, 11, 1), SEASONS,
                        espn_season_year=2026) == "2025-26"
    assert season_label("nfl", datetime.date(2026, 9, 13), SEASONS,
                        espn_season_year=2026) == "2026-27"


def test_espn_moves_a_preseason_game_into_the_season_it_belongs_to():
    assert season_label("nba", datetime.date(2026, 10, 5), SEASONS,
                        espn_season_year=2027) == "2026-27"
    assert season_label("nfl", datetime.date(2026, 8, 15), SEASONS,
                        espn_season_year=2026) == "2026-27"


@pytest.mark.parametrize("bogus", [2030, 1999, 0, -5])
def test_an_implausible_espn_year_loses_to_the_date(bogus):
    """A season containing a date can only be named for the year before it,
    that year, or the year after. Anything else is a bad value from the feed
    -- and 0 or a negative would ask for a year that does not exist."""
    assert season_label("nfl", datetime.date(2026, 9, 20), SEASONS,
                        espn_season_year=bogus) == "2026-27"


def test_a_configured_leap_day_does_not_raise():
    """02-29 is absent from three years in four; the day clamps back."""
    seasons = {"nfl": {"start": "09-05", "end": "02-29"}}
    assert season_label("nfl", datetime.date(2026, 9, 13), seasons) == "2026-27"


def test_a_single_year_season_is_just_the_year():
    """mlb runs March to October and boxing runs all year; neither crosses."""
    assert season_label("mlb", datetime.date(2026, 6, 1), SEASONS) == "2026"
    assert season_label("boxing", datetime.date(2026, 12, 31), SEASONS) == "2026"


def test_the_format_matches_the_history_already_stored():
    """1,237 nba rows are already "2025-26"; the canonical form is the one
    that does not require rewriting them."""
    assert season_label("nba", datetime.date(2025, 11, 1), SEASONS) == "2025-26"


def test_an_unknown_sport_falls_back_to_the_year_rather_than_crashing():
    assert season_label("quidditch", datetime.date(2026, 6, 1), SEASONS) == "2026"


def test_malformed_config_falls_back_to_the_year():
    bad = {"nfl": {"start": "not-a-date", "end": "02-10"}}
    assert season_label("nfl", datetime.date(2026, 9, 13), bad) == "2026"


def test_a_decade_boundary_keeps_two_digits():
    seasons = {"nfl": {"start": "09-05", "end": "02-10"}}
    assert season_label("nfl", datetime.date(2029, 10, 1), seasons) == "2029-30"
    assert season_label("nfl", datetime.date(2030, 1, 5), seasons) == "2029-30"


# --- the write sites actually use it ----------------------------------------

from backend.collectors.espn import SPORT_URLS
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import fetch_and_store_games
from backend.tests.test_espn_game_identity import _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

NFL_SB = SPORT_URLS["nfl"]
JAN = datetime.date(2027, 1, 24)


def _db(tmp_path):
    db = tmp_path / "sl.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="BUF Team", abbreviation="BUF", sport="nfl"),
        Team(id=2, name="DET Team", abbreviation="DET", sport="nfl"),
    ])
    session.commit()
    session.close()
    return str(db)


@pytest.mark.asyncio
async def test_a_january_game_is_stored_under_the_season_it_belongs_to(tmp_path, httpx_mock):
    """The live shape of the bug: the ESPN path read the game's own year."""
    db = _db(tmp_path)
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={JAN.strftime('%Y%m%d')}",
        json={"events": [_event("801", "BUF", "DET", "STATUS_FINAL", 31, 10,
                                when="2027-01-25T00:00Z")]})
    session = get_session(get_engine(db))
    await fetch_and_store_games(session, ["nfl"], JAN, reconcile=False)
    session.close()

    session = get_session(get_engine(db))
    try:
        assert session.query(Game).one().season == "2026-27"
    finally:
        session.close()


AUG = datetime.date(2026, 8, 15)


@pytest.mark.asyncio
async def test_a_preseason_game_is_stored_under_the_season_it_precedes(
        tmp_path, httpx_mock):
    """End to end, through the path `backfill_date_range` uses.

    The scheduler never asks about an August nfl date because it filters on
    `is_sport_in_season`. The backfill takes explicit dates and does not, so
    this is the door the mislabelled rows would come through.
    """
    db = _db(tmp_path)
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={AUG.strftime('%Y%m%d')}",
        json={"events": [_event("802", "BUF", "DET", "STATUS_FINAL", 17, 14,
                                when="2026-08-16T00:00Z",
                                season_type=1, season_year=2026)]})
    session = get_session(get_engine(db))
    await fetch_and_store_games(session, ["nfl"], AUG, reconcile=False)
    session.close()

    session = get_session(get_engine(db))
    try:
        game = session.query(Game).one()
        assert game.season == "2026-27"
        assert game.season_type == "preseason"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_an_event_without_a_season_block_still_gets_a_label(
        tmp_path, httpx_mock):
    """ESPN's year is an improvement on the derivation, not a dependency of
    it. `_event` omits the block entirely when neither field is passed."""
    db = _db(tmp_path)
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={AUG.strftime('%Y%m%d')}",
        json={"events": [_event("803", "BUF", "DET", "STATUS_FINAL", 17, 14,
                                when="2026-08-16T00:00Z")]})
    session = get_session(get_engine(db))
    await fetch_and_store_games(session, ["nfl"], AUG, reconcile=False)
    session.close()

    session = get_session(get_engine(db))
    try:
        assert session.query(Game).one().season == "2026-27"
    finally:
        session.close()
