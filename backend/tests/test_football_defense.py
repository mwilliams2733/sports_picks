"""NFL prop matchup: the opponent's pass / run defense moves the projection.

Before 2026-10-04 football props had no matchup adjustment: the analyzer
read `defensive_rating`, a basketball stat never computed for football.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.analysis import football_defense as fd
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.database import get_session
from backend.models import Base, Game, PlayerProp, PlayerStat, Team

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def test_shrunk_factor_pulls_a_short_record_toward_the_league():
    """Two games at 300 against a league of 200: (600 + 3*200) / 5 = 240."""
    assert fd.shrunk_factor([300.0, 300.0], [200.0] * 32) == pytest.approx(1.2)


def test_no_factor_before_the_league_has_a_week_of_games():
    assert fd.shrunk_factor([300.0], [200.0] * 31) is None


def test_adjust_moves_the_projection_by_half_the_factor():
    """Measured c ~ 0.5: a defense allowing 20% more moves the projection 10%."""
    assert fd.adjust(250.0, 1.2) == pytest.approx(275.0)


def test_only_yardage_markets_get_a_factor():
    allowed = {1: {"pass": [200.0] * 40, "rush": [100.0] * 40}}

    assert fd.matchup_factor(allowed, 1, "player_pass_yds") == pytest.approx(1.0)
    assert fd.matchup_factor(allowed, 1, "player_anytime_td") is None


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="Kansas City Chiefs", abbreviation="KC", sport="nfl"),
               Team(id=2, name="Buffalo Bills", abbreviation="BUF", sport="nfl"),
               Team(id=3, name="Detroit Lions", abbreviation="DET", sport="nfl")])
    s.commit()
    yield s
    s.close()


def _game(s, gid, d, home, away, season="2026", status="final"):
    s.add(Game(id=gid, sport="nfl", season=season, date=d, status=status,
               home_team_id=home, away_team_id=away))


def _log(s, team, d, pass_yards=None, rush_yards=None, name="P"):
    s.add(PlayerStat(player_name=f"{name}{team}{d}", team_id=team, sport="nfl",
                     stat_type="game_log", game_date=d, pass_yards=pass_yards,
                     rush_yards=rush_yards, source="test", fetched_at=NOW))


def test_yards_allowed_charges_each_offense_to_the_other_defense(session):
    _game(session, 1, date(2026, 9, 13), home=1, away=2)
    _log(session, 1, date(2026, 9, 13), pass_yards=300.0)
    _log(session, 1, date(2026, 9, 13), rush_yards=80.0, name="R")
    _log(session, 2, date(2026, 9, 13), pass_yards=150.0)
    session.commit()

    allowed = fd.yards_allowed(session, "nfl", "2026", date(2026, 9, 20))

    assert allowed[2] == {"pass": [300.0], "rush": [80.0]}   # BUF's defense faced KC
    assert allowed[1] == {"pass": [150.0], "rush": [0.0]}


def test_a_game_log_dated_a_day_off_still_matches(session):
    """Game dates and box-score dates can differ by a day (UTC vs local)."""
    _game(session, 1, date(2026, 9, 14), home=1, away=2)
    _log(session, 1, date(2026, 9, 13), pass_yards=300.0)
    session.commit()

    assert fd.yards_allowed(session, "nfl", "2026", date(2026, 9, 20))[2]["pass"] == [300.0]


def test_only_this_seasons_final_games_strictly_before_the_date(session):
    _game(session, 1, date(2026, 9, 13), home=1, away=2)
    _game(session, 2, date(2026, 9, 20), home=1, away=3)                     # the day itself
    _game(session, 3, date(2026, 1, 4), home=1, away=2, season="2025")       # last season
    _game(session, 4, date(2026, 9, 17), home=2, away=3, status="scheduled")
    for gid_date in (date(2026, 9, 13), date(2026, 9, 20), date(2026, 1, 4), date(2026, 9, 17)):
        _log(session, 1, gid_date, pass_yards=100.0)
        _log(session, 2, gid_date, pass_yards=100.0)
    session.commit()

    allowed = fd.yards_allowed(session, "nfl", "2026", date(2026, 9, 20))

    assert allowed[2]["pass"] == [100.0]
    assert 3 not in allowed


def _prop(line):
    return PlayerProp(game_id=1, bookmaker="draftkings", market="player_pass_yds",
                      player_name="QB", outcome="Over", line=line, odds=-110, fetched_at=NOW)


def _logs(values):
    return [PlayerStat(player_name="QB", team_id=1, sport="nfl", stat_type="game_log",
                       game_date=date(2026, 9, 7) + timedelta(days=7 * i), pass_yards=v, source="test",
                       fetched_at=NOW) for i, v in enumerate(values)]


def test_the_analyzer_applies_the_factor_to_the_projection():
    recent = _logs([240.0, 260.0, 250.0, 255.0, 245.0])
    analyzer = PropAnalyzer()

    plain = analyzer.analyze(_prop(240.5), None, recent)
    soft = analyzer.analyze(_prop(240.5), None, recent, matchup_factor=1.2)

    assert soft.projection == pytest.approx(fd.adjust(plain.projection, 1.2), abs=0.11)
    assert soft.projection > plain.projection


def test_receiving_and_passing_face_the_pass_defense_rushing_the_run_defense():
    allowed = {1: {"pass": [300.0] * 40, "rush": [100.0] * 40},
               2: {"pass": [100.0] * 40, "rush": [100.0] * 40}}

    pass_f = fd.matchup_factor(allowed, 1, "player_pass_yds")
    assert fd.matchup_factor(allowed, 1, "player_reception_yds") == pass_f > 1.0
    assert fd.matchup_factor(allowed, 1, "player_rush_yds") == pytest.approx(1.0)


# --- the pipeline passes the factor through ---------------------------------

class _NoStats:
    async def fetch_player_stats(self, sport, abbreviation):
        return None, None

    def store_stats(self, *a, **k):
        return 0

    async def close(self):
        pass


def test_the_prop_pipeline_gives_nfl_props_their_opponents_factor(session, monkeypatch):
    import asyncio

    from backend.pipeline import prop_pipeline

    day = date(2026, 9, 27)
    session.add(Team(id=4, name="Texas Rangers", abbreviation="TEX", sport="mlb"))
    session.add(Team(id=5, name="Houston Astros", abbreviation="HOU", sport="mlb"))
    _game(session, 10, day, home=1, away=2, status="scheduled")
    session.add(Game(id=11, sport="mlb", season="2026", date=day, status="scheduled",
                     home_team_id=4, away_team_id=5))
    for gid, team, player in ((10, 1, "QB"), (11, 4, "Batter")):
        session.add(PlayerStat(player_name=player, team_id=team, sport="x",
                               stat_type="season_avg", source="test", fetched_at=NOW))
        session.add(PlayerProp(game_id=gid, bookmaker="draftkings", market="player_pass_yds",
                               player_name=player, outcome="Over", line=240.5, odds=-110,
                               fetched_at=NOW))
    session.commit()

    allowed = {2: {"pass": [300.0, 300.0], "rush": []},
               3: {"pass": [200.0] * 40, "rush": []}}
    monkeypatch.setattr(prop_pipeline.football_defense, "yards_allowed",
                        lambda s, sport, season, before: allowed)
    seen = {}

    class _Recording:
        def __init__(self, **kw):
            pass

        def analyze(self, prop, *a, matchup_factor=None, **k):
            seen[prop.player_name] = matchup_factor

    monkeypatch.setattr(prop_pipeline, "PropAnalyzer", _Recording)

    asyncio.run(prop_pipeline._run_prop_pipeline_inner(session, _NoStats(), day, None))

    assert seen["QB"] == pytest.approx(fd.matchup_factor(allowed, 2, "player_pass_yds"))
    assert seen["QB"] > 1.0                      # BUF (id 2), the opponent, allows more
    assert seen["Batter"] is None                # not football
