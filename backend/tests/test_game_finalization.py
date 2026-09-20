"""A game that has been played must end up marked final.

`morning_scout` runs at 8/9/10am ET and asked ESPN about `today` only -- before
any of that day's games had been played. The next morning it asked about the
new today. Nothing ever revisited a past date, so a score could only land by an
accident of timing the schedule never produced, and 570 games with past dates
sat `scheduled` forever.
"""
import datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from backend.models import Base, Game, Team
from backend.pipeline.scheduler import morning_scout

from backend.time_utils import et_today


#: pytest-httpx 0.36 configures the fixture through a MARKER, not through
#: fixture overrides. These tests register a stub per (sport, day) in the
#: window; which ones get used is the behaviour under test, so an unused stub
#: is not a failure -- the assertions on game status are. A day may also
#: legitimately be requested more than once.
pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)


@pytest.fixture(autouse=True)
def _no_window_runs(monkeypatch):
    """morning_scout runs a window synchronously when its start time is
    already past (`scheduler.py:196`), and `_run_window` fetches player stats
    through nba_api -- which uses `requests`, not `httpx`, so `httpx_mock`
    cannot intercept it. That means two real 30s reads to stats.nba.com and a
    65s test. Window running is not what these tests are about."""
    from backend.pipeline import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module, "_run_window",
                        lambda *a, **k: None)


@pytest.fixture
def scheduler():
    """morning_scout calls scheduler.get_jobs(), so it needs a real one. Never
    started: starting it would spawn cron threads inside the suite."""
    return BackgroundScheduler()

NBA_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
NCAAB_SB = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
            "mens-college-basketball/scoreboard")

TODAY = et_today()
YESTERDAY = TODAY - datetime.timedelta(days=1)


def _event(home_abbr, away_abbr, status, home_score=None, away_score=None,
           when=None, event_id="1"):
    """One ESPN scoreboard event, in the shape fetch_scoreboard parses."""
    def side(abbr, home_away, score):
        c = {"homeAway": home_away,
             "team": {"abbreviation": abbr, "displayName": f"{abbr} Team"}}
        if score is not None:
            c["score"] = str(score)
        return c

    return {
        "id": event_id,
        "date": f"{(when or TODAY).isoformat()}T23:00Z",
        "status": {"type": {"name": status}},
        "competitions": [{"competitors": [
            side(home_abbr, "home", home_score),
            side(away_abbr, "away", away_score),
        ]}],
    }


def _seed_game(session, home_abbr, away_abbr, when, *, sport="nba",
               status="scheduled", home_id=None, away_id=None):
    Base.metadata.create_all(session.get_bind())
    for tid, abbr in ((home_id, home_abbr), (away_id, away_abbr)):
        if session.query(Team).filter_by(abbreviation=abbr, sport=sport).first():
            continue
        session.add(Team(id=tid, name=f"{abbr} Team", abbreviation=abbr, sport=sport))
    session.flush()
    h = session.query(Team).filter_by(abbreviation=home_abbr, sport=sport).one()
    a = session.query(Team).filter_by(abbreviation=away_abbr, sport=sport).one()
    game = Game(sport=sport, season=f"{when.year}-{when.year + 1}", date=when,
                home_team_id=h.id, away_team_id=a.id, status=status)
    session.add(game)
    session.commit()
    return game


def _config():
    """Seasons wide enough that nba and ncaab are in season on any date."""
    return {"seasons": {
        "nba": {"start": "01-01", "end": "12-31"},
        "ncaab": {"start": "01-01", "end": "12-31"},
    }, "odds_budget": {}}


def _stub(httpx_mock, url, day, events):
    httpx_mock.add_response(
        url=f"{url}?dates={day.strftime('%Y%m%d')}", json={"events": events})


def test_a_game_played_yesterday_is_finalized_today(db_session, httpx_mock, scheduler):
    """The whole bug in one test."""
    game = _seed_game(db_session, "CLE", "NY", YESTERDAY, home_id=1, away_id=2)
    assert game.status == "scheduled" and game.home_score is None

    _stub(httpx_mock, NBA_SB, TODAY, [])
    _stub(httpx_mock, NBA_SB, YESTERDAY,
          [_event("CLE", "NY", "STATUS_FINAL", 110, 105, when=YESTERDAY)])
    for d in (2, 3):
        _stub(httpx_mock, NBA_SB, TODAY - datetime.timedelta(days=d), [])
    for d in range(0, 4):
        _stub(httpx_mock, NCAAB_SB, TODAY - datetime.timedelta(days=d), [])

    morning_scout(_config(), db_session.get_bind(), scheduler)

    db_session.expire_all()
    row = db_session.query(Game).one()
    assert row.status == "final"
    assert (row.home_score, row.away_score) == (110, 105)


def test_a_lookback_day_never_cancels_a_row_it_could_not_match(db_session, httpx_mock, scheduler):
    """Finalize-only.

    `_reconcile_against_espn` matches on an unordered team-id pair, so
    abbreviation drift or a duplicate team row makes a real game look absent.
    On a past date that row would be marked `canceled` instead of `final` --
    converting a matching failure into data loss, on exactly the rows a
    lookback exists to rescue.
    """
    _seed_game(db_session, "CLE", "NY", YESTERDAY, home_id=1, away_id=2)
    _seed_game(db_session, "LAL", "BOS", YESTERDAY, home_id=3, away_id=4)

    _stub(httpx_mock, NBA_SB, TODAY, [])
    # ESPN lists only the first game.
    _stub(httpx_mock, NBA_SB, YESTERDAY,
          [_event("CLE", "NY", "STATUS_FINAL", 110, 105, when=YESTERDAY)])
    for d in (2, 3):
        _stub(httpx_mock, NBA_SB, TODAY - datetime.timedelta(days=d), [])
    for d in range(0, 4):
        _stub(httpx_mock, NCAAB_SB, TODAY - datetime.timedelta(days=d), [])

    morning_scout(_config(), db_session.get_bind(), scheduler)

    db_session.expire_all()
    listed = db_session.query(Game).filter(Game.home_team_id == 1).one()
    unlisted = db_session.query(Game).filter(Game.home_team_id == 3).one()
    assert listed.status == "final"
    assert unlisted.status == "scheduled", (
        "a lookback day cancelled a row it merely failed to match")


def test_todays_pass_still_reconciles(db_session, httpx_mock, scheduler):
    """The lookback must not weaken today's behaviour: a postponed game still
    has to drop out of Today's Picks."""
    _seed_game(db_session, "CLE", "NY", TODAY, home_id=1, away_id=2)

    # Non-empty, but a different game: reconciliation runs and finds ours absent.
    _stub(httpx_mock, NBA_SB, TODAY,
          [_event("LAL", "BOS", "STATUS_SCHEDULED", when=TODAY, event_id="9")])
    for d in (1, 2, 3):
        _stub(httpx_mock, NBA_SB, TODAY - datetime.timedelta(days=d), [])
    for d in range(0, 4):
        _stub(httpx_mock, NCAAB_SB, TODAY - datetime.timedelta(days=d), [])

    morning_scout(_config(), db_session.get_bind(), scheduler)

    db_session.expire_all()
    row = db_session.query(Game).filter(Game.home_team_id == 1).one()
    assert row.status == "canceled"


def test_an_in_season_college_game_is_fetched_too(db_session, httpx_mock, scheduler):
    """ncaab had 81 past games stuck non-final because scheduled_sports was
    hard-coded to ("nba", "nfl"). ESPN's scoreboard needs no API key, so
    breadth here costs wall-clock, not budget."""
    _seed_game(db_session, "DUKE", "UNC", YESTERDAY, sport="ncaab",
               home_id=11, away_id=12)

    for d in range(0, 4):
        _stub(httpx_mock, NBA_SB, TODAY - datetime.timedelta(days=d), [])
    _stub(httpx_mock, NCAAB_SB, TODAY, [])
    _stub(httpx_mock, NCAAB_SB, YESTERDAY,
          [_event("DUKE", "UNC", "STATUS_FINAL", 80, 74, when=YESTERDAY)])
    for d in (2, 3):
        _stub(httpx_mock, NCAAB_SB, TODAY - datetime.timedelta(days=d), [])

    morning_scout(_config(), db_session.get_bind(), scheduler)

    db_session.expire_all()
    row = db_session.query(Game).filter(Game.sport == "ncaab").one()
    assert row.status == "final"
    assert (row.home_score, row.away_score) == (80, 74)
