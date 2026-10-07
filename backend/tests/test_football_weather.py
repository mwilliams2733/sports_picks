"""NFL weather: the forecast rule, the rain-Under tracking picks, prop factors.

Measured by `backend.scripts.weather_forecast_backtest` on 2026-10-06.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.analysis import football_weather as fw
from backend.collectors import weather as cw
from backend.database import get_session, migrate_weather_strategy, run_migrations
from backend.models import (WEATHER_RAIN_UNDER_STRATEGY_ID, Base, Game, GameWeather,
                            Odds, PickModel, PickResult, StrategyModel, Team)

KICKOFF = datetime(2026, 10, 11, 17, 0)        # naive UTC, as Game.start_time


# --- the rule -------------------------------------------------------------------

def test_only_open_air_stadiums_have_game_weather():
    assert fw.outdoor_stadium("CHI")["stadium"] == "Soldier Field"
    assert fw.outdoor_stadium("DET") is None          # dome
    assert fw.outdoor_stadium("DAL") is None          # retractable: a game-day call
    assert fw.outdoor_stadium("XXX") is None


def test_wet_and_windy_thresholds_are_inclusive():
    assert fw.is_wet(1.0) and not fw.is_wet(0.99) and not fw.is_wet(None)
    assert fw.is_windy(15.0) and not fw.is_windy(14.9)


def test_prop_factor_applies_only_measured_effects():
    assert fw.prop_factor("player_pass_yds", 2.0, 20.0) == pytest.approx(1 - 0.15 - 0.14)
    assert fw.prop_factor("player_reception_yds", 2.0, 5.0) == pytest.approx(0.90)
    assert fw.prop_factor("player_rush_yds", 5.0, 30.0) is None    # not significant
    assert fw.prop_factor("player_pass_yds", 0.0, 5.0) is None     # fair weather


# --- the forecast summary ----------------------------------------------------------

def _payload(start: datetime, precip, wind=(10.0, 10.0, 10.0, 10.0), temp=50.0):
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(len(precip))]
    return {"hourly": {"time": times, "temperature_2m": [temp] * len(precip),
                       "precipitation": list(precip), "wind_speed_10m": list(wind)}}


def test_summary_covers_the_kickoff_hour_and_the_two_after():
    start = KICKOFF - timedelta(hours=1)
    p = _payload(start, precip=(9.0, 0.4, 0.4, 0.4, 9.0), wind=(1, 12, 15, 18, 1))
    ko = KICKOFF.replace(minute=25, tzinfo=timezone.utc)   # a 1:25 kickoff floors to 1:00
    temp, precip, wind = cw.summarize_hours(p, ko)
    assert precip == pytest.approx(1.2)
    assert wind == pytest.approx(15.0)


def test_summary_refuses_a_missing_hour():
    p = _payload(KICKOFF, precip=(0.5, 0.5))
    assert cw.summarize_hours(p, KICKOFF.replace(tzinfo=timezone.utc)) is None


# --- storage and picks -------------------------------------------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    s = get_session(engine)
    Base.metadata.create_all(engine)
    migrate_weather_strategy(engine)
    s.add_all([Team(id=1, name="Chicago Bears", abbreviation="CHI", sport="nfl"),
               Team(id=2, name="Detroit Lions", abbreviation="DET", sport="nfl"),
               Team(id=3, name="Green Bay Packers", abbreviation="GB", sport="nfl")])
    s.commit()
    yield s
    s.close()


def _game(s, gid, home, away=2, neutral=False, start=KICKOFF, status="scheduled"):
    s.add(Game(id=gid, sport="nfl", season="2026-27", date=start.date() if start else date(2026, 10, 11),
               start_time=start, home_team_id=home, away_team_id=away,
               neutral_site=neutral, status=status))


def _odds(s, gid, line=44.5, over=-110, under=-110):
    s.add(Odds(game_id=gid, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
               over_under=line, over_price=over, under_price=under))


def test_the_seeded_strategy_is_inactive_and_cannot_be_the_game_strategy(session):
    row = session.get(StrategyModel, WEATHER_RAIN_UNDER_STRATEGY_ID)
    assert (row.name, row.is_active, row.strategy_type) == ("weather_rain_under", False, "tracking")
    migrate_weather_strategy(session.get_bind())                   # idempotent
    assert session.query(StrategyModel).filter_by(name="weather_rain_under").count() == 1


def test_run_migrations_seeds_it_on_a_fresh_database():
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    assert get_session(engine).get(StrategyModel, WEATHER_RAIN_UNDER_STRATEGY_ID) is not None


def test_collect_skips_neutral_roofed_and_untimed_games(session, monkeypatch):
    _game(session, 1, home=1)                      # CHI outdoors
    _game(session, 2, home=2, away=1)              # DET dome
    _game(session, 3, home=3, neutral=True)        # neutral site
    _game(session, 4, home=3, start=None)          # no kickoff yet
    session.commit()
    seen = []

    async def fake(client, lat, lon, ko):
        seen.append((lat, lon, ko))
        return (50.0, 2.0, 11.0)

    monkeypatch.setattr(cw, "fetch_forecast", fake)
    out = asyncio.run(cw.collect_game_weather(session, session.query(Game).all()))

    assert set(out) == {1}
    assert seen[0][2] == KICKOFF.replace(tzinfo=timezone.utc)
    assert session.query(GameWeather).one().stadium == "Soldier Field"


def _weather(s, gid, precip, captured=None):
    w = GameWeather(game_id=gid, kickoff=KICKOFF, stadium="x", temp_f=50.0,
                    precip_mm=precip, wind_mph=8.0,
                    captured_at=captured or datetime.now(timezone.utc))
    s.add(w)
    s.commit()
    return w


def _rule_picks(s):
    return s.query(PickModel).filter_by(strategy_id=WEATHER_RAIN_UNDER_STRATEGY_ID).all()


def test_a_wet_forecast_stores_a_tracking_under_at_the_consensus(session):
    _game(session, 1, home=1)
    _odds(session, 1, line=44.5, over=-105, under=-115)
    session.commit()
    w = _weather(session, 1, precip=2.0)

    fw.rain_under_picks(session, session.query(Game).all(), {1: w})

    (pick,) = _rule_picks(session)
    assert (pick.pick_type, pick.pick_value, pick.odds_at_pick) == ("over_under", "Under 44.5", -115)
    assert pick.tracking_only is True
    assert json.loads(pick.rationale_json)["weather_id"] == w.id
    # -115 is 0.5349 with the vig; against a -105 Over (0.5122) it is 0.5108 fair.
    assert pick.market_prob_novig == pytest.approx(0.5108, abs=1e-3)


def test_a_dry_forecast_stores_nothing_and_withdraws_an_earlier_wet_pick(session):
    _game(session, 1, home=1, start=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2))
    _odds(session, 1)
    session.commit()
    fw.rain_under_picks(session, session.query(Game).all(), {1: _weather(session, 1, 3.0)})
    assert _rule_picks(session)[0].withdrawn_at is None

    fw.rain_under_picks(session, session.query(Game).all(), {1: _weather(session, 1, 0.2)})

    (pick,) = _rule_picks(session)
    assert pick.withdrawn_at is not None


def test_a_graded_rule_pick_is_never_rewritten(session):
    _game(session, 1, home=1)
    _odds(session, 1, line=44.5)
    session.commit()
    fw.rain_under_picks(session, session.query(Game).all(), {1: _weather(session, 1, 3.0)})
    (pick,) = _rule_picks(session)
    session.add(PickResult(pick_id=pick.id, result="win", payout=0.91))
    session.query(Odds).update({"over_under": 41.0})
    session.commit()

    fw.rain_under_picks(session, session.query(Game).all(), {1: _weather(session, 1, 3.0)})

    assert _rule_picks(session)[0].pick_value == "Under 44.5"


def test_latest_weather_is_the_last_capture(session):
    _game(session, 1, home=1)
    session.commit()
    _weather(session, 1, 5.0, captured=datetime(2026, 10, 11, 10, tzinfo=timezone.utc))
    _weather(session, 1, 0.0, captured=datetime(2026, 10, 11, 15, tzinfo=timezone.utc))
    assert cw.latest_weather(session, [1])[1].precip_mm == 0.0


def test_the_model_filter_excludes_the_rule(session):
    _game(session, 1, home=1)
    _odds(session, 1)
    session.add(StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True))
    session.add(PickModel(game_id=1, strategy_id=1, pick_type="over_under", pick_value="Over 44.5",
                          confidence=1, edge_pct=4.0, created_at=datetime.now(timezone.utc)))
    session.commit()
    fw.rain_under_picks(session, session.query(Game).all(), {1: _weather(session, 1, 3.0)})

    model = session.query(PickModel).filter(PickModel.by_model()).all()
    assert [p.pick_value for p in model] == ["Over 44.5"]


def test_weekly_review_keeps_the_two_records_apart(session):
    from backend.scripts.weekly_review import model_bets
    _game(session, 1, home=1, status="final")
    _odds(session, 1)
    session.add(StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True))
    model = PickModel(game_id=1, strategy_id=1, pick_type="over_under", pick_value="Over 44.5",
                      confidence=1, edge_pct=4.0, tracking_only=True,
                      created_at=datetime.now(timezone.utc))
    rule = PickModel(game_id=1, strategy_id=WEATHER_RAIN_UNDER_STRATEGY_ID, pick_type="over_under",
                     pick_value="Under 44.5", confidence=1, edge_pct=0.0, tracking_only=True,
                     created_at=datetime.now(timezone.utc))
    session.add_all([model, rule])
    session.flush()
    session.add_all([PickResult(pick_id=model.id, result="loss", payout=-1.0),
                     PickResult(pick_id=rule.id, result="win", payout=0.91)])
    session.commit()

    assert [b.result for b in model_bets(session, tracking=True)] == ["loss"]
    assert [b.result for b in model_bets(session, tracking=True, rain_rule=True)] == ["win"]


# --- props ---------------------------------------------------------------------------

def test_the_analyzer_multiplies_by_the_weather_factor():
    from backend.analysis.prop_analyzer import PropAnalyzer
    from backend.models import PlayerProp, PlayerStat
    prop = PlayerProp(game_id=1, bookmaker="dk", market="player_pass_yds", player_name="QB",
                      outcome="Over", line=240.5, odds=-110, fetched_at=KICKOFF)
    recent = [PlayerStat(player_name="QB", team_id=1, sport="nfl", stat_type="game_log",
                         game_date=date(2026, 9, 7) + timedelta(days=7 * i), pass_yards=v,
                         source="t", fetched_at=KICKOFF)
              for i, v in enumerate([240.0, 260.0, 250.0, 255.0, 245.0])]
    a = PropAnalyzer(min_edge=-1000)
    plain = a.analyze(prop, None, recent)
    wet = a.analyze(prop, None, recent, weather_factor=0.86)
    assert wet.projection == pytest.approx(plain.projection * 0.86)


# --- wiring ----------------------------------------------------------------------------

def test_an_nfl_window_captures_weather_and_applies_the_rule(session, monkeypatch):
    from backend.pipeline import scheduler as sched
    _game(session, 1, home=1)
    session.commit()
    calls = []

    async def fake_collect(s, games):
        calls.append(("collect", [g.id for g in games]))
        return {}

    async def fake_props(*a, **k):
        return {}

    monkeypatch.setattr(sched, "collect_game_weather", fake_collect)
    monkeypatch.setattr(sched, "rain_under_picks",
                        lambda s, games, w: calls.append(("rule", [g.id for g in games])))
    monkeypatch.setattr(sched, "run_prop_pipeline", fake_props)
    monkeypatch.setattr(sched, "get_session", lambda engine: session)

    sched._run_window({"odds_api_key": None}, object(), "nfl", {"games": [{"id": 1}]})
    assert calls == [("collect", [1]), ("rule", [1])]

    calls.clear()
    sched._run_window({"odds_api_key": None}, object(), "mlb", {"games": [{"id": 1}]})
    assert calls == []                                  # nfl only


def test_the_prop_pipeline_passes_the_latest_forecast(session, monkeypatch):
    from backend.models import PlayerProp, PlayerStat
    from backend.pipeline import prop_pipeline
    day = KICKOFF.date()
    _game(session, 1, home=1)
    session.add(PlayerStat(player_name="QB", team_id=1, sport="nfl", stat_type="season_avg",
                           source="t", fetched_at=KICKOFF))
    session.add(PlayerProp(game_id=1, bookmaker="dk", market="player_pass_yds", player_name="QB",
                           outcome="Over", line=240.5, odds=-110, fetched_at=KICKOFF))
    session.commit()
    _weather(session, 1, precip=3.0)

    class _NoStats:
        async def fetch_player_stats(self, sport, abbreviation):
            return None, None

        def store_stats(self, *a, **k):
            return 0

    async def no_roster(client, label):
        return None

    monkeypatch.setattr(prop_pipeline.football_injuries, "fetch_roster", no_roster)
    seen = {}

    class _Recording:
        def __init__(self, **kw):
            pass

        def analyze(self, prop, *a, weather_factor=None, **k):
            seen[prop.player_name] = weather_factor

    monkeypatch.setattr(prop_pipeline, "PropAnalyzer", _Recording)
    asyncio.run(prop_pipeline._run_prop_pipeline_inner(session, _NoStats(), day, None))
    assert seen["QB"] == pytest.approx(0.86)
