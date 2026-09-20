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

from backend.config import season_label

SEASONS = {
    "nfl": {"start": "09-05", "end": "02-10"},
    "ncaaf": {"start": "08-24", "end": "01-20"},
    "nba": {"start": "10-22", "end": "06-20"},
    "mlb": {"start": "03-27", "end": "10-31"},
    "boxing": {"start": "01-01", "end": "12-31"},
}


def test_a_cross_year_season_is_named_for_the_year_it_started():
    assert season_label("nfl", datetime.date(2026, 9, 13), SEASONS) == "2026-27"


def test_a_game_after_new_year_keeps_its_season(): 
    """The defect in `f"{y}-{y+1}"`: January 2027 is the 2026 season."""
    assert season_label("nfl", datetime.date(2027, 1, 24), SEASONS) == "2026-27"
    assert season_label("ncaaf", datetime.date(2027, 1, 10), SEASONS) == "2026-27"
    assert season_label("nba", datetime.date(2027, 5, 2), SEASONS) == "2026-27"


def test_a_game_just_before_the_start_belongs_to_the_previous_season():
    assert season_label("nfl", datetime.date(2026, 9, 4), SEASONS) == "2025-26"
    assert season_label("nfl", datetime.date(2026, 9, 5), SEASONS) == "2026-27"


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
