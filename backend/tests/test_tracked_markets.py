"""Spread and total picks are generated again, but tracked, not published.

Re-measured 2026-10-03, the margin and totals models still lose to the
market line in every in-season sport. NFL is worse by 0.62 points of
margin across 1188 games and 264 dates. So `*_VALIDATED_SPORTS` stays
empty. The owner chose to generate the picks anyway, for one purpose: to
measure closing line value going forward on real prices.

Three properties matter, and each is a test below:

* A tracked pick is made only on a real quoted price, never the -110
  fallback. CLV against an invented price measures nothing.
* Grading records the real closing price, so price-CLV exists.
* Nothing a person sees or that moves money reads a tracked pick: not the
  email, the website, the bankroll or the recalibration. They share one
  filter, `PickModel.published()`.
"""
import datetime
import json

import pytest
from fastapi.testclient import TestClient

import backend.analysis.variants.ensemble as ens
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, OddsSnapshot, TeamStats
from backend.database import get_engine, get_session, run_migrations
from backend.models import Base, Game, PickModel, PickResult, StrategyModel, Team

D = datetime.date(2026, 11, 1)


# -- generation -------------------------------------------------------------

def _stats(point_diff, pf=24.0, pa=20.0):
    return TeamStats(
        point_diff=point_diff, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=7,
        points_for=pf, points_against=pa)


def _game(sport="nfl", *, priced=True, total=30.5):
    price = (lambda p: p) if priced else (lambda p: None)
    return GameData(
        game_id=1, sport=sport, date=D, home_team_id=1, away_team_id=2,
        home_stats=_stats(10.0, pf=30.0, pa=17.0),
        away_stats=_stats(-5.0, pf=27.0, pa=28.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-200,
                           moneyline_away=170, spread_home=-3.5,
                           spread_away=3.5, over_under=total,
                           spread_home_price=price(-105),
                           spread_away_price=price(-115),
                           over_price=price(-102), under_price=price(-118))])


def _picks(game, market):
    cfg = {"min_edge": 5.0, "max_edge": 100.0}
    return [p for p in EnsembleStrategy("ensemble", cfg).predict(game)
            if p.pick_type == market]


def test_the_tracked_sports_are_the_in_season_ones_and_none_is_validated():
    assert ens.SPREAD_TRACKED_SPORTS == frozenset({"nfl", "mlb", "ncaaf"})
    assert ens.TOTALS_TRACKED_SPORTS == frozenset({"nfl", "mlb", "ncaaf"})
    assert ens.SPREAD_VALIDATED_SPORTS == frozenset()
    assert ens.TOTALS_VALIDATED_SPORTS == frozenset()


@pytest.mark.parametrize("market", ["spread", "over_under"])
def test_a_tracked_sport_yields_a_tracking_pick_at_its_real_price(market):
    picks = _picks(_game("nfl"), market)

    assert len(picks) == 1
    assert picks[0].tracking_only is True
    assert picks[0].odds_at_pick in (-105, -115, -102, -118), (
        "a tracked pick must carry the quoted price, not the -110 fallback")


@pytest.mark.parametrize("market", ["spread", "over_under"])
def test_no_quoted_price_means_no_tracked_pick(market):
    """The fallback price is what made all 241 old picks useless for CLV."""
    assert _picks(_game("nfl", priced=False), market) == []


@pytest.mark.parametrize("market", ["spread", "over_under"])
def test_a_sport_neither_tracked_nor_validated_yields_nothing(market):
    assert _picks(_game("nba"), market) == []


def test_a_validated_sport_publishes_rather_than_tracks(monkeypatch):
    monkeypatch.setattr(ens, "SPREAD_VALIDATED_SPORTS", frozenset({"nfl"}))

    picks = _picks(_game("nfl"), "spread")

    assert len(picks) == 1
    assert picks[0].tracking_only is False


# -- storage and sizing -----------------------------------------------------

@pytest.fixture()
def gen_session(tmp_path):
    from backend.models import Odds
    s = get_session(get_engine(str(tmp_path / "g.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=D, status="scheduled",
               home_team_id=1, away_team_id=2))
    s.add(Odds(game_id=1, bookmaker="bk", moneyline_home=-150, moneyline_away=130,
               spread_home=-3.5, spread_away=3.5, over_under=44.5,
               spread_home_price=-105, spread_away_price=-115,
               over_price=-102, under_price=-118))
    s.add(StrategyModel(id=1, name="ensemble", strategy_type="game", is_active=True,
                        config_json='{"min_edge": 0.0, "max_edge": 100.0, "max_odds": null}'))
    s.commit()
    return s


def _stub_predict(monkeypatch, picks):
    from backend.data_types import Pick
    monkeypatch.setattr(EnsembleStrategy, "predict", lambda self, g: [
        Pick(game_id=g.game_id, implied_probability=0.5, **p) for p in picks])


# 0.62 at -150 sizes to about 1.25u, inside the unit clamp, so a discount
# would show. At 0.66 the stake hits MAX_UNIT either way and hides it.
ML = dict(pick_type="moneyline", pick_value="HOME ML", confidence=3, edge_pct=6.0,
          model_probability=0.62, odds_at_pick=-150)
SPREAD = dict(pick_type="spread", pick_value="HOME -3.5", confidence=3, edge_pct=8.0,
              model_probability=0.58, odds_at_pick=-105, tracking_only=True)


def test_the_flag_is_stored(gen_session, monkeypatch):
    from backend.pipeline.pick_generator import generate_and_store_picks
    _stub_predict(monkeypatch, [ML, SPREAD])

    generate_and_store_picks(gen_session, 1, D)

    stored = {p.pick_type: p.tracking_only for p in gen_session.query(PickModel)}
    assert stored == {"moneyline": False, "spread": True}


def test_a_tracked_pick_does_not_shrink_the_published_stake(gen_session, monkeypatch):
    """Sizing shrinks each stake when one game carries several picks, since
    they are one bet on one outcome. A tracked pick is not a bet."""
    from backend.pipeline.pick_generator import generate_and_store_picks
    _stub_predict(monkeypatch, [ML])
    generate_and_store_picks(gen_session, 1, D)
    alone = gen_session.query(PickModel).one().suggested_unit_size
    gen_session.query(PickModel).delete()
    gen_session.commit()

    _stub_predict(monkeypatch, [ML, SPREAD])
    generate_and_store_picks(gen_session, 1, D)

    ml = gen_session.query(PickModel).filter_by(pick_type="moneyline").one()
    assert 0 < alone < 3, "the fixture must size inside the clamp to test anything"
    assert ml.suggested_unit_size == pytest.approx(alone)


def test_a_refresh_restates_the_flag(gen_session, monkeypatch):
    """A sport validated mid-day turns its open tracked pick into a
    published one on the next refresh, and the row must say so."""
    from backend.pipeline.pick_generator import generate_and_store_picks
    _stub_predict(monkeypatch, [SPREAD])
    generate_and_store_picks(gen_session, 1, D)
    _stub_predict(monkeypatch, [{**SPREAD, "odds_at_pick": -112, "tracking_only": False}])

    generate_and_store_picks(gen_session, 1, D)

    pick = gen_session.query(PickModel).one()
    assert (pick.odds_at_pick, pick.tracking_only) == (-112, False)


# -- closing price ----------------------------------------------------------

@pytest.mark.parametrize("pick_type,pick_value,want", [
    ("spread", "HOME -3.5", -108), ("spread", "AWAY +3.5", -112),
    ("over_under", "Over 44.5", -104), ("over_under", "Under 44.5", -116),
])
def test_grading_records_the_real_closing_price(monkeypatch, pick_type, pick_value, want):
    import backend.analysis.line_snapshots as ls
    from backend.pipeline.grader import capture_closing_odds
    monkeypatch.setattr(ls, "closing_consensus", lambda s, gid: {
        "moneyline_home": -150, "moneyline_away": 130,
        "spread_home": -4.0, "spread_away": 4.0, "over_under": 45.0,
        "spread_home_price": -108, "spread_away_price": -112,
        "over_price": -104, "under_price": -116})
    result = PickResult(pick_id=1, result="win", payout=0.95)

    capture_closing_odds(None, result, 1, pick_type, pick_value, odds_at_pick=-105,
                         price_is_quoted=True)

    assert result.odds_at_close == want


def test_an_unquoted_closing_price_stays_absent(monkeypatch):
    """Not odds_at_pick, which would report zero movement as a measurement."""
    import backend.analysis.line_snapshots as ls
    from backend.pipeline.grader import capture_closing_odds
    monkeypatch.setattr(ls, "closing_consensus", lambda s, gid: {
        "moneyline_home": -150, "moneyline_away": 130,
        "spread_home": -4.0, "spread_away": 4.0, "over_under": 45.0,
        "spread_home_price": None, "spread_away_price": None,
        "over_price": None, "under_price": None})
    result = PickResult(pick_id=1, result="win", payout=0.95)

    capture_closing_odds(None, result, 1, "spread", "HOME -3.5", odds_at_pick=-105,
                         price_is_quoted=True)

    assert result.odds_at_close is None
    assert result.line_at_close == -4.0


def test_a_legacy_fallback_priced_pick_keeps_the_copy(monkeypatch):
    """The 241 old picks were priced at an invented -110. A real close
    against that would fabricate movement, and backfill_closing_lines
    re-runs this on them."""
    import backend.analysis.line_snapshots as ls
    from backend.pipeline.grader import capture_closing_odds
    monkeypatch.setattr(ls, "closing_consensus", lambda s, gid: {
        "moneyline_home": -150, "moneyline_away": 130,
        "spread_home": -4.0, "spread_away": 4.0, "over_under": 45.0,
        "spread_home_price": -125, "spread_away_price": 105,
        "over_price": -125, "under_price": 105})
    result = PickResult(pick_id=1, result="win", payout=0.95)

    capture_closing_odds(None, result, 1, "spread", "HOME -3.5", odds_at_pick=-110)

    assert result.odds_at_close == -110


def test_grading_a_tracked_pick_records_its_real_close(book, monkeypatch):
    """Through the scheduler's real grading path, not the helper alone."""
    import backend.analysis.line_snapshots as ls
    from backend.pipeline.scheduler import grade_pending_picks
    _, engine = book
    s = get_session(engine)
    s.query(PickResult).delete()
    s.query(Game).filter_by(id=1).update(
        {"status": "final", "home_score": 27, "away_score": 20})
    s.commit()
    monkeypatch.setattr(ls, "closing_consensus", lambda sess, gid: {
        "moneyline_home": -160, "moneyline_away": 140,
        "spread_home": -4.0, "spread_away": 4.0, "over_under": 45.0,
        "spread_home_price": -108, "spread_away_price": -112,
        "over_price": -104, "under_price": -116})

    grade_pending_picks(s)

    closes = {pr.pick_id: pr.odds_at_close for pr in s.query(PickResult)}
    assert closes == {1: -160, 2: -108}


def test_backfilling_closes_keeps_a_tracked_picks_real_price(book, monkeypatch):
    import backend.analysis.line_snapshots as ls
    from backend.backfill_closing_lines import backfill
    path, engine = book
    monkeypatch.setattr(ls, "closing_consensus", lambda sess, gid: {
        "moneyline_home": -160, "moneyline_away": 140,
        "spread_home": -4.0, "spread_away": 4.0, "over_under": 45.0,
        "spread_home_price": -108, "spread_away_price": -112,
        "over_price": -104, "under_price": -116})

    backfill(path, dry_run=False)

    closes = {pr.pick_id: pr.odds_at_close for pr in get_session(engine).query(PickResult)}
    assert closes[2] == -108


# -- nothing public reads a tracked pick ------------------------------------

@pytest.fixture()
def book(tmp_path):
    """One published moneyline and one tracked spread on the same day, both
    graded. Every reader below must see the moneyline and only it."""
    path = str(tmp_path / "b.db")
    engine = get_engine(path)
    run_migrations(engine)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="ensemble", strategy_type="game",
                        is_active=True, config_json="{}"))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=D, status="scheduled",
               home_team_id=1, away_team_id=2))
    s.flush()
    s.add_all([
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=5, edge_pct=6.0,
                  odds_at_pick=-150, model_prob=0.66),
        PickModel(id=2, game_id=1, strategy_id=1, pick_type="spread",
                  pick_value="HOME -3.5", confidence=5, edge_pct=8.0,
                  odds_at_pick=-105, model_prob=0.58, tracking_only=True),
    ])
    s.flush()
    # The tracked pick has a closing line, so a CLV figure that read it
    # would count it.
    s.add_all([PickResult(pick_id=1, result="win", payout=0.6667),
               PickResult(pick_id=2, result="loss", payout=-1.0,
                          line_at_close=-4.0, odds_at_close=-110)])
    s.commit()
    s.close()
    return path, engine


def test_the_digest_skips_a_tracked_pick(book):
    from backend.digest.selector import select_digest
    _, engine = book
    s = get_session(engine)
    bar = {"min_odds": -100_000, "max_odds": 100_000, "max_game_picks": 10,
           "max_props": 10, "blend_weight": {}}

    sections = select_digest(s, D, ["nfl"], {"nfl": {"start": "09-05", "end": "02-10"}},
                             send_bar=bar)

    assert [p.pick_id for p in sections[0].picks] == [1]


def test_the_trailing_record_skips_a_tracked_pick(book):
    from backend.digest.selector import trailing_record
    _, engine = book

    assert trailing_record(get_session(engine), "nfl", D + datetime.timedelta(days=1)) == (1, 0)


def test_the_bankroll_ignores_a_tracked_result(book):
    from backend.pipeline.pick_generator import _bankroll_state
    _, engine = book

    current, _ = _bankroll_state(get_session(engine))

    assert current == pytest.approx(100.6667)


def test_the_digest_health_check_counts_published_picks_only(book):
    """Otherwise an empty digest day with tracked picks reads as
    EMPTY_BUT_PICKS_EXIST, the alarm for a slate landing too late."""
    from backend.scripts.check_digest import picks_for
    path, _ = book

    assert picks_for(path, D) == 1


def test_recalibration_ignores_a_tracked_result(book, monkeypatch):
    """Both picks are tier 5. Counting the tracked loss would read the tier
    as 50% and move its threshold on a pick nobody was told about."""
    import backend.analysis.recalibrator as rc
    _, engine = book
    monkeypatch.setattr(rc, "MIN_PICKS_PER_TIER", 1)
    monkeypatch.setattr(rc, "DEVIATION_THRESHOLD", 0.0)

    rc.Recalibrator(get_session(engine), "nfl").run()

    from backend.models import CalibrationHistory
    rows = get_session(engine).query(CalibrationHistory).all()
    assert [(r.confidence_tier, r.actual_win_rate, r.sample_size) for r in rows] == [(5, 1.0, 1)]


@pytest.fixture()
def client(book):
    from backend.api.main import create_app
    path, _ = book
    with TestClient(create_app(path)) as c:
        yield c


def test_todays_picks_hide_a_tracked_pick(client):
    rows = client.get("/picks/today", params={"target_date": D.isoformat()}).json()
    assert [r["pick_type"] for r in rows] == ["moneyline"]


def test_a_day_of_only_tracked_picks_rolls_over_to_tomorrow(client, book, monkeypatch):
    """With no target date, an empty today shows tomorrow. Tracked picks
    must not make today look non-empty."""
    import backend.api.picks as picks_api
    _, engine = book
    s = get_session(engine)
    s.query(PickModel).filter_by(id=1).update({"tracking_only": True})
    s.add(Game(id=2, sport="nfl", season="2026", date=D + datetime.timedelta(days=1),
               status="scheduled", home_team_id=1, away_team_id=2))
    s.flush()
    s.add(PickModel(id=3, game_id=2, strategy_id=1, pick_type="moneyline",
                    pick_value="AWAY ML", confidence=4, edge_pct=5.0, odds_at_pick=120))
    s.commit()
    monkeypatch.setattr(picks_api, "et_today", lambda: D)

    rows = client.get("/picks/today").json()

    assert [r["id"] for r in rows] == [3]


def test_pick_history_hides_a_tracked_pick(client):
    rows = client.get("/picks/history").json()
    assert [r["pick_type"] for r in rows] == ["moneyline"]


def test_the_record_and_daily_figures_exclude_a_tracked_pick(client):
    record = client.get("/stats/record").json()
    assert (record["wins"], record["losses"]) == (1, 0)
    daily = client.get("/stats/daily").json()
    assert [(d["wins"], d["losses"]) for d in daily] == [(1, 0)]


def test_calibration_and_clv_stats_exclude_a_tracked_pick(client):
    cal = client.get("/stats/calibration").json()
    assert cal["total_graded"] == 1
    clv = client.get("/stats/clv").json()
    assert clv["line_clv"]["total_picks"] == 0


# -- analysis still sees it ---------------------------------------------------

def test_the_clv_report_includes_tracked_picks(book):
    from backend.analysis import clv_report
    _, engine = book

    samples = clv_report.load_samples(get_session(engine))

    assert {(s.market, s.tracking_only) for s in samples} == {
        ("moneyline", False), ("spread", True)}


def test_the_clv_report_never_pools_tracked_with_published():
    """A tracked group is labelled and summarised on its own, ALL included."""
    from backend.analysis.clv_report import ClvSample, group_report
    samples = [ClvSample(1, 1, "nfl", "spread", 1.0, False, depth=2),
               ClvSample(2, 2, "nfl", "spread", -1.0, False, depth=2, tracking_only=True)]

    groups = group_report(samples, ("spread",))

    assert set(groups) == {"nfl", "ALL", "nfl (tracked)", "ALL (tracked)"}
    assert groups["ALL"].n == 1 and groups["ALL (tracked)"].n == 1
