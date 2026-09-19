"""A game is identified by ESPN's event id, not by (date, teams).

ESPN timestamps events in UTC but groups its scoreboard by Eastern date, so a
game starting after 8pm ET is filed a day late by a naive `.date()`. Matching
on (sport, date, teams) then cannot connect the same game arriving under the
other convention, and production ended up with MIA/ORL twice: id 13 scheduled
on 2026-03-14 and id 1015 final on 2026-03-15.

`collectors/espn.py:40` has always returned `espn_id` on every event. There was
simply nowhere to put it.
"""
import asyncio
import datetime

import pytest

from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import fetch_and_store_games

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

NBA_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ET_DAY = datetime.date(2026, 3, 14)      # the date ESPN files it under
UTC_DAY = datetime.date(2026, 3, 15)     # the date a naive parser produces


def _event(event_id, home, away, status, home_score=None, away_score=None,
           when="2026-03-15T00:00Z", neutral=None):
    def side(abbr, home_away, score):
        c = {"homeAway": home_away,
             "team": {"abbreviation": abbr, "displayName": f"{abbr} Team"}}
        if score is not None:
            c["score"] = str(score)
        return c

    return {
        "id": event_id,
        "date": when,
        "status": {"type": {"name": status}},
        "competitions": [{
            "competitors": [
                side(home, "home", home_score),
                side(away, "away", away_score),
            ],
            # Omitted entirely when `neutral is None`, so the collector's
            # absent-field default stays exercised by the existing callers.
            **({} if neutral is None else {"neutralSite": neutral}),
        }],
    }


def _seed_teams(session):
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="MIA Team", abbreviation="MIA", sport="nba"),
        Team(id=2, name="ORL Team", abbreviation="ORL", sport="nba"),
    ])
    session.flush()


def test_a_game_is_matched_by_espn_id_even_when_its_stored_date_is_wrong(
        db_session, httpx_mock):
    """The production case, reproduced.

    The row sits on the UTC date and is still `scheduled`. ESPN returns the
    same event id as final. Matching on (sport, date, teams) would miss it and
    insert a twin; matching on espn_id finds it, finalizes it, and corrects the
    date.
    """
    _seed_teams(db_session)
    # The row sits on the EASTERN date. The event's timestamp is
    # 2026-03-15T00:00Z, which the current UTC-based parser resolves to
    # 2026-03-15 -- so (sport, date, teams) cannot match it and only espn_id
    # can. That asymmetry is the bug, and Task 2 fixes the parser separately.
    db_session.add(Game(sport="nba", season="2025-26", date=ET_DAY,
                        espn_id="401700001", home_team_id=1, away_team_id=2,
                        status="scheduled"))
    db_session.commit()

    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260314",
        json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                117, 121)]})

    asyncio.run(fetch_and_store_games(db_session, ["nba"], ET_DAY,
                                      reconcile=False))

    db_session.expire_all()
    rows = db_session.query(Game).all()
    assert len(rows) == 1, "a twin was inserted instead of matching on espn_id"
    assert rows[0].status == "final"
    assert (rows[0].home_score, rows[0].away_score) == (117, 121)


def test_two_legitimate_games_between_the_same_teams_stay_separate(
        db_session, httpx_mock):
    """MLB plays three-game series, so the same pair on consecutive days is
    normal rather than a duplicate -- 12 of production's 36 adjacent pairs are
    mlb. Distinct event ids must remain distinct rows."""
    _seed_teams(db_session)
    db_session.add(Game(sport="nba", season="2025-26", date=ET_DAY,
                        espn_id="401700001", home_team_id=1, away_team_id=2,
                        status="final", home_score=100, away_score=99))
    db_session.commit()

    # The NEXT day's game in the series. Same pair, different event, different
    # date -- which is what a series actually looks like. Two distinct games on
    # the SAME date would defeat the (date, teams) fallback, and that is a real
    # limitation for MLB doubleheaders, noted rather than tested here.
    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260315",
        json={"events": [_event("401700002", "MIA", "ORL", "STATUS_FINAL",
                                110, 105, when="2026-03-15T23:00Z")]})

    asyncio.run(fetch_and_store_games(db_session, ["nba"], UTC_DAY,
                                      reconcile=False))

    db_session.expire_all()
    ids = {g.espn_id for g in db_session.query(Game).all()}
    assert ids == {"401700001", "401700002"}


def test_an_existing_row_without_an_espn_id_acquires_one(db_session, httpx_mock):
    """Every row predates this column. They must pick it up as they are seen,
    or the backfill is the only way they ever get one."""
    _seed_teams(db_session)
    db_session.add(Game(sport="nba", season="2025-26", date=UTC_DAY,
                        home_team_id=1, away_team_id=2, status="scheduled"))
    db_session.commit()

    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260315",
        json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                117, 121, when="2026-03-15T18:00Z")]})

    asyncio.run(fetch_and_store_games(db_session, ["nba"], UTC_DAY,
                                      reconcile=False))

    db_session.expire_all()
    rows = db_session.query(Game).all()
    assert len(rows) == 1
    assert rows[0].espn_id == "401700001"


def test_a_new_game_is_stored_with_its_espn_id(db_session, httpx_mock):
    _seed_teams(db_session)

    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260315",
        json={"events": [_event("401700009", "MIA", "ORL", "STATUS_SCHEDULED",
                                when="2026-03-15T18:00Z")]})

    asyncio.run(fetch_and_store_games(db_session, ["nba"], UTC_DAY,
                                      reconcile=False))

    db_session.expire_all()
    assert db_session.query(Game).one().espn_id == "401700009"
