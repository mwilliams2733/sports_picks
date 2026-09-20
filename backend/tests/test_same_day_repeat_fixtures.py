"""Two games between the same teams on the same day are two games.

`fetch_and_store_games` looks a game up by espn_id, then falls back to
(sport, date, home, away) for rows that have no espn_id. That fallback had
no espn_id constraint, so it also matched rows that already carried a
DIFFERENT one -- and a genuinely distinct second meeting was folded into the
first and lost.

Confirmed against ESPN: mlb 2026-05-23 CIN v STL is two events (401873647 at
17:10Z, 401815461 at 23:15Z) sharing an Eastern date, and only the first is
stored. Same for BAL/DET on 2026-05-24, and for the 2026 NBA All-Star
Championship, which repeated the round-robin's STRIPES v STARS pairing.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import fetch_and_store_games

DAY = datetime.date(2026, 5, 23)
MLB_SB = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)


def _event(event_id, when, home_score, away_score):
    def side(abbr, ha, score):
        return {"homeAway": ha,
                "team": {"abbreviation": abbr, "displayName": f"{abbr} Team"},
                "score": str(score)}
    return {
        "id": event_id, "date": when,
        "status": {"type": {"name": "STATUS_FINAL"}},
        "season": {"type": 2},
        "competitions": [{
            "competitors": [side("CIN", "home", home_score),
                            side("STL", "away", away_score)],
            "type": {"id": "1", "abbreviation": "STD"},
        }],
    }


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "d.db")))
    Base.metadata.create_all(s.get_bind())
    return s


def _run(session, httpx_mock, events, day=DAY):
    import asyncio
    httpx_mock.add_response(
        url=f"{MLB_SB}?dates={day.strftime('%Y%m%d')}", json={"events": events})
    return asyncio.run(fetch_and_store_games(session, ["mlb"], day,
                                             reconcile=False))


def test_a_doubleheader_stores_both_games(session, httpx_mock):
    """The regression. The second game used to be folded into the first."""
    _run(session, httpx_mock, [
        _event("401873647", "2026-05-23T17:10Z", 1, 8),
        _event("401815461", "2026-05-23T23:15Z", 4, 2),
    ])
    games = session.query(Game).all()
    assert len(games) == 2
    assert {g.espn_id for g in games} == {"401873647", "401815461"}


def test_each_game_keeps_its_own_score(session, httpx_mock):
    """Folding them also meant one game wore the other's result."""
    _run(session, httpx_mock, [
        _event("401873647", "2026-05-23T17:10Z", 1, 8),
        _event("401815461", "2026-05-23T23:15Z", 4, 2),
    ])
    by_id = {g.espn_id: (g.home_score, g.away_score)
             for g in session.query(Game)}
    assert by_id["401873647"] == (1, 8)
    assert by_id["401815461"] == (4, 2)


def test_re_seeing_the_same_event_still_updates_rather_than_duplicates(
    session, httpx_mock
):
    """The espn_id path must still be idempotent."""
    for _ in range(2):
        _run(session, httpx_mock, [_event("401873647", "2026-05-23T17:10Z", 1, 8)])
    assert session.query(Game).count() == 1


def test_a_row_without_an_espn_id_is_still_adopted(session, httpx_mock):
    """The fallback's real purpose: rows from the Odds API carry no ESPN id."""
    session.add_all([Team(id=1, name="CIN Team", abbreviation="CIN", sport="mlb"),
                     Team(id=2, name="STL Team", abbreviation="STL", sport="mlb")])
    session.flush()
    session.add(Game(id=99, sport="mlb", season="2026", date=DAY,
                     status="scheduled", home_team_id=1, away_team_id=2))
    session.commit()

    _run(session, httpx_mock, [_event("401873647", "2026-05-23T17:10Z", 1, 8)])

    games = session.query(Game).all()
    assert len(games) == 1, "created a duplicate instead of adopting the row"
    assert games[0].id == 99 and games[0].espn_id == "401873647"
