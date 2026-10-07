"""NFL props: a missing teammate (or QB) moves the projection.

Effects measured by `backend.scripts.injury_experiment` on 2026-10-06.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.analysis import football_injuries as fi
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.database import get_session
from backend.models import Base, Game, PlayerProp, PlayerStat, Team

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
LEADERS = {"qb": "Quarterback", "target": "Wideout", "carry": "Lead Back"}


def _roster(**status):
    return {name: None for name in ("Quarterback", "Wideout", "Lead Back", "Backup",
                                    "Slot")} | status


# --- who is absent -----------------------------------------------------------

def test_parse_roster_reads_the_current_injury_under_both_names():
    payload = {"athletes": [{"items": [
        {"displayName": "Baker Mayfield", "fullName": "Baker Mayfield",
         "injuries": [{"status": "Out"}]},
        {"displayName": "Travis Etienne Jr.", "fullName": "Travis Etienne",
         "injuries": [{"status": "Injured Reserve"}]},
        {"displayName": "Healthy Guy", "fullName": "Healthy Guy", "injuries": []},
    ]}]}
    roster = fi.parse_roster(payload)
    assert roster["Baker Mayfield"] == "Out"
    assert roster["Travis Etienne Jr."] == roster["Travis Etienne"] == "Injured Reserve"
    assert roster["Healthy Guy"] is None


@pytest.mark.parametrize("status,absent", [
    ("Out", True), ("Doubtful", True), ("Injured Reserve", True), ("Suspension", True),
    ("Questionable", False), ("Day-To-Day", False), (None, False)])
def test_absent_unless_the_status_may_still_play(status, absent):
    assert fi.is_absent({"P": status}, "P") is absent


def test_off_the_roster_is_absent_and_no_leader_is_not():
    assert fi.is_absent({"P": None}, "Released Guy")
    assert not fi.is_absent({"P": None}, None)


# --- the factor ----------------------------------------------------------------

def test_lead_back_out_lifts_the_other_backs():
    roster = _roster(**{"Lead Back": "Out"})
    assert fi.injury_factor("player_rush_yds", "Backup", LEADERS, roster) == pytest.approx(1.26)


def test_the_missing_leader_himself_gets_nothing():
    roster = _roster(**{"Lead Back": "Out"})
    assert fi.injury_factor("player_rush_yds", "Lead Back", LEADERS, roster) is None


def test_receiver_gains_from_the_top_target_and_loses_from_the_qb():
    roster = _roster(**{"Wideout": "Injured Reserve", "Quarterback": "Out"})
    assert fi.injury_factor("player_reception_yds", "Slot", LEADERS, roster) == \
        pytest.approx(1.0 + 0.17 - 0.07)


def test_a_missing_qb_does_not_move_rushing():
    """Measured +0.02, p 0.77: not applied."""
    roster = _roster(Quarterback="Out")
    assert fi.injury_factor("player_rush_yds", "Backup", LEADERS, roster) is None


def test_no_factor_without_a_roster_or_for_unmeasured_markets():
    assert fi.injury_factor("player_rush_yds", "Backup", LEADERS, None) is None
    roster = _roster(Quarterback="Out", Wideout="Out", **{"Lead Back": "Out"})
    assert fi.injury_factor("player_pass_yds", "Backup", LEADERS, roster) is None
    assert fi.injury_factor("player_rush_yds", "Backup", LEADERS, _roster()) is None


# --- season leaders from game logs ----------------------------------------------

@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="Tampa Bay Buccaneers", abbreviation="TB", sport="nfl"),
               Team(id=2, name="Dallas Cowboys", abbreviation="DAL", sport="nfl")])
    s.add(Game(id=1, sport="nfl", season="2026-27", date=date(2026, 9, 13),
               status="final", home_team_id=1, away_team_id=2))
    s.add(Game(id=9, sport="nfl", season="2025-26", date=date(2025, 9, 7),
               status="final", home_team_id=1, away_team_id=2))
    s.commit()
    yield s
    s.close()


def _log(s, name, team, d, **yards):
    s.add(PlayerStat(player_name=name, team_id=team, sport="nfl", stat_type="game_log",
                     game_date=d, source="test", fetched_at=NOW, **yards))


def test_leaders_are_this_seasons_yards_strictly_before_the_game(session):
    _log(session, "Starter", 1, date(2026, 9, 13), rush_yards=90.0)
    _log(session, "Starter", 1, date(2026, 9, 20), rush_yards=10.0)
    _log(session, "Backup", 1, date(2026, 9, 20), rush_yards=60.0)
    _log(session, "Backup", 1, date(2026, 10, 4), rush_yards=200.0)    # the game itself
    _log(session, "Old Star", 1, date(2025, 12, 1), rush_yards=900.0)  # last season
    _log(session, "Rival", 2, date(2026, 9, 20), rush_yards=500.0)     # other team
    session.commit()

    leaders = fi.season_leaders(session, 1, "2026-27", date(2026, 10, 4))

    assert leaders["carry"] == "Starter"      # 100 beats 60; the 200 is not yet played
    assert "qb" not in leaders                # no passing yards yet


# --- the analyzer and pipeline apply it ------------------------------------------

def _prop(market="player_rush_yds", player="Backup", game_id=1, line=40.5):
    return PlayerProp(game_id=game_id, bookmaker="draftkings", market=market,
                      player_name=player, outcome="Over", line=line, odds=-110,
                      fetched_at=NOW)


def test_the_analyzer_multiplies_the_projection():
    recent = [PlayerStat(player_name="Backup", team_id=1, sport="nfl", stat_type="game_log",
                         game_date=date(2026, 9, 7) + timedelta(days=7 * i), rush_yards=v,
                         source="test", fetched_at=NOW)
              for i, v in enumerate([40.0, 45.0, 35.0, 50.0, 30.0])]
    plain = PropAnalyzer(min_edge=-1000).analyze(_prop(), None, recent)
    hurt = PropAnalyzer(min_edge=-1000).analyze(_prop(), None, recent, injury_factor=1.26)
    assert hurt.projection == pytest.approx(plain.projection * 1.26)


class _NoStats:
    async def fetch_player_stats(self, sport, abbreviation):
        return None, None

    def store_stats(self, *a, **k):
        return 0

    async def close(self):
        pass


def test_the_prop_pipeline_passes_each_players_factor(session, monkeypatch):
    import asyncio

    from backend.pipeline import prop_pipeline

    day = date(2026, 10, 4)
    session.add(Game(id=2, sport="nfl", season="2026-27", date=day, status="scheduled",
                     home_team_id=1, away_team_id=2))
    _log(session, "Lead Back", 1, date(2026, 9, 13), rush_yards=100.0)
    _log(session, "Backup", 1, date(2026, 9, 13), rush_yards=30.0)
    _log(session, "Dallas Back", 2, date(2026, 9, 13), rush_yards=80.0)
    for name, team in (("Backup", 1), ("Dallas Back", 2)):
        session.add(PlayerStat(player_name=name, team_id=team, sport="nfl",
                               stat_type="season_avg", source="test", fetched_at=NOW))
        session.add(_prop(player=name, game_id=2))
    session.commit()

    rosters = {"TB": {"Lead Back": "Out", "Backup": None},
               "DAL": {"Dallas Back": None}}

    async def fake_fetch(client, label):
        return rosters[label]

    monkeypatch.setattr(prop_pipeline.football_injuries, "fetch_roster", fake_fetch)
    seen = {}

    class _Recording:
        def __init__(self, **kw):
            pass

        def analyze(self, prop, *a, injury_factor=None, **k):
            seen[prop.player_name] = injury_factor

    monkeypatch.setattr(prop_pipeline, "PropAnalyzer", _Recording)

    asyncio.run(prop_pipeline._run_prop_pipeline_inner(session, _NoStats(), day, None))

    assert seen["Backup"] == pytest.approx(1.26)   # TB's lead back is out
    assert seen["Dallas Back"] is None             # nobody missing for DAL
