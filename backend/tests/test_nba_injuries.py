"""NBA props: when the team's star is out, teammates' projections move.

Measured by `backend.scripts.nba_injury_experiment` on 2026-10-07.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.analysis import nba_injuries as ni
from backend.analysis.football_injuries import parse_roster
from backend.database import get_session
from backend.models import Base, Game, PlayerProp, PlayerStat, Team

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
START = date(2026, 10, 20)


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="Milwaukee Bucks", abbreviation="MIL", sport="nba"),
               Team(id=2, name="New York Knicks", abbreviation="NY", sport="nba")])
    s.flush()
    s.add(Game(id=1, sport="nba", season="2026-27", date=START, status="final",
               home_team_id=1, away_team_id=2))
    s.add(Game(id=9, sport="nba", season="2025-26", date=date(2026, 3, 1), status="final",
               home_team_id=1, away_team_id=2))
    s.commit()
    yield s
    s.close()


def _log(s, name, d, points, minutes=30.0, team=1):
    s.add(PlayerStat(player_name=name, team_id=team, sport="nba", stat_type="game_log",
                     game_date=d, points=points, rebounds=5.0, assists=3.0, minutes=minutes,
                     source="test", fetched_at=NOW))


def _season(s, n, star_games=None):
    """n team games; the star plays the first `star_games` of them."""
    star_games = n if star_games is None else star_games
    days = [START + timedelta(days=2 * i) for i in range(n)]
    for i, d in enumerate(days):
        _log(s, "Role", d, 12.0)
        if i < star_games:
            _log(s, "Giannis", d, 30.0)
    s.commit()
    return days


def test_parse_roster_reads_the_nba_flat_layout():
    payload = {"athletes": [{"displayName": "Giannis Antetokounmpo",
                             "fullName": "Giannis Antetokounmpo",
                             "injuries": [{"status": "Out"}]},
                            {"displayName": "Role Player", "injuries": []}]}
    assert parse_roster(payload) == {"Giannis Antetokounmpo": "Out", "Role Player": None}


def test_no_star_before_ten_games(session):
    days = _season(session, 9)
    assert ni.season_star(session, 1, "2026-27", days[-1] + timedelta(days=2)) is None


def test_the_star_is_this_seasons_ppg_leader_from_earlier_games(session):
    days = _season(session, 12)
    for i in range(ni.MIN_STAR_GAMES):                             # last season: ignored
        _log(session, "Old Star", date(2026, 3, 1) + timedelta(days=i), 50.0)
    _log(session, "Role", days[-1] + timedelta(days=2), 400.0)    # the game itself: ignored
    session.commit()
    assert ni.season_star(session, 1, "2026-27", days[-1] + timedelta(days=2)) == "Giannis"


def test_out_needs_the_roster_and_a_recent_appearance(session):
    days = _season(session, 12)
    tonight = days[-1] + timedelta(days=2)
    assert ni.star_out(session, 1, "2026-27", tonight, {"Giannis": "Out"}) == "Giannis"
    assert ni.star_out(session, 1, "2026-27", tonight, {"Giannis": "Day-To-Day"}) is None
    assert ni.star_out(session, 1, "2026-27", tonight, None) is None


def test_a_star_gone_for_weeks_is_not_out_tonight(session):
    days = _season(session, 20, star_games=12)                    # missed the last 8
    tonight = days[-1] + timedelta(days=2)
    assert ni.season_star(session, 1, "2026-27", tonight) == "Giannis"
    assert ni.star_out(session, 1, "2026-27", tonight, {"Giannis": "Out"}) is None


def test_the_factor_applies_only_to_teammates_on_measured_markets():
    assert ni.injury_factor("player_points", "Role", "Giannis") == pytest.approx(1.124)
    assert ni.injury_factor("player_rebounds", "Role", "Giannis") == pytest.approx(1.064)
    assert ni.injury_factor("player_points", "Giannis", "Giannis") is None
    assert ni.injury_factor("player_threes", "Role", "Giannis") is None
    assert ni.injury_factor("player_points", "Role", None) is None


class _NoStats:
    async def fetch_player_stats(self, sport, abbreviation):
        return None, None

    def store_stats(self, *a, **k):
        return 0


def test_the_prop_pipeline_passes_the_nba_factor(session, monkeypatch):
    from backend.pipeline import prop_pipeline
    days = _season(session, 12)
    tonight = days[-1] + timedelta(days=2)
    session.add(Game(id=2, sport="nba", season="2026-27", date=tonight, status="scheduled",
                     home_team_id=1, away_team_id=2))
    session.add(PlayerStat(player_name="Role", team_id=1, sport="nba", stat_type="season_avg",
                           source="t", fetched_at=NOW))
    session.add(PlayerProp(game_id=2, bookmaker="dk", market="player_points", player_name="Role",
                           outcome="Over", line=12.5, odds=-110, fetched_at=NOW))
    session.commit()
    rosters = {"MIL": {"Giannis": "Out", "Role": None}, "NY": {}}

    async def fake_fetch(client, label, sport="nfl"):
        assert sport == "nba"
        return rosters[label]

    monkeypatch.setattr(prop_pipeline.football_injuries, "fetch_roster", fake_fetch)
    seen = {}

    class _Recording:
        def __init__(self, **kw):
            pass

        def analyze(self, prop, *a, injury_factor=None, **k):
            seen[prop.player_name] = injury_factor

    monkeypatch.setattr(prop_pipeline, "PropAnalyzer", _Recording)
    asyncio.run(prop_pipeline._run_prop_pipeline_inner(session, _NoStats(), tonight, None))
    assert seen["Role"] == pytest.approx(1.124)
