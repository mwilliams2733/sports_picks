"""One prop pick per (game, player, market, strategy), refreshed, never re-added.

Measured 2026-09-28: the 2026-09-26 ncaaf slate carried 4,836 prop picks for
132 players. Two multipliers:

* every window run re-inserted the whole day's props -- 18 runs, 18 copies;
* every alternate line was a pick of its own -- Over 204.5 / 205.5 / 207.5 /
  211.5 pass yards for one quarterback, four "bets" on one outcome.

One player-market reached 72 rows, each graded as a 1u wager. Grading them
moved `_bankroll_state` from 128.93u to -121.24u in one pass.

Game picks already solve the first half (`generate_and_store_picks`: refresh
in place while ungraded and unstarted). Props now follow the same rule, using
the same `_refreshable`.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.data_types import PropAnalysis
from backend.database import get_session
from backend.models import Base, Game, PickModel, PickResult, Team
from backend.pipeline.prop_pipeline import (_one_per_player_market,
                                            _store_prop_picks)

DAY = date(2026, 9, 26)


def _a(line=205.5, odds=-110, edge=10.0, outcome="Over", player="Julian Lewis",
       market="player_pass_yds", game_id=1, bookmaker="draftkings"):
    return PropAnalysis(
        player_name=player, market=market, line=line, outcome=outcome,
        season_avg=220.0, recent_avg=220.0, projection=220.0, edge_pct=edge,
        confidence=3, source="espn", is_stale=False, game_id=game_id,
        odds=odds, bookmaker=bookmaker)


# --- one line per player-market ---------------------------------------------

def test_alternate_lines_collapse_to_one_pick():
    picked = _one_per_player_market([
        _a(line=204.5, odds=-121, edge=12.0), _a(line=205.5, odds=-112, edge=11.0),
        _a(line=207.5, odds=-115, edge=10.0), _a(line=211.5, odds=-114, edge=8.0)])

    assert len(picked) == 1


def test_the_kept_line_is_the_one_with_the_most_edge_not_the_best_price():
    """Across lines, price alone picks the WORSE bet: Over 211.5 at -114
    pays more than Over 204.5 at -121, but needs seven more yards. Edge is
    measured against the line, so it is the comparison that means something."""
    picked = _one_per_player_market([
        _a(line=211.5, odds=-114, edge=8.0), _a(line=204.5, odds=-121, edge=12.0)])

    assert picked[0].line == 204.5


def test_an_edge_tie_goes_to_the_better_price_whatever_the_order():
    a, b = _a(line=205.5, odds=-120, edge=10.0), _a(line=206.5, odds=+100, edge=10.0)

    assert _one_per_player_market([a, b])[0].odds == 100
    assert _one_per_player_market([b, a])[0].odds == 100


def test_different_markets_players_and_games_stay_separate():
    picked = _one_per_player_market([
        _a(), _a(market="player_rush_yds"), _a(player="Arch Manning"),
        _a(game_id=2)])

    assert len(picked) == 4


# --- storage: refresh, never re-add -----------------------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="ncaaf"),
               Team(id=2, name="A", abbreviation="A", sport="ncaaf")])
    s.flush()
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    s.add_all([
        Game(id=1, sport="ncaaf", season="2026", date=DAY, status="scheduled",
             home_team_id=1, away_team_id=2, start_time=future.replace(tzinfo=None)),
        Game(id=2, sport="ncaaf", season="2026", date=DAY, status="scheduled",
             home_team_id=1, away_team_id=2, start_time=past.replace(tzinfo=None)),
    ])
    s.commit()
    yield s
    s.close()


def _props(session):
    return session.query(PickModel).filter(PickModel.pick_type == "prop").all()


def test_a_second_run_does_not_add_a_second_pick(session):
    _store_prop_picks(session, [_a()], strategy_id=7)
    _store_prop_picks(session, [_a()], strategy_id=7)

    assert len(_props(session)) == 1


def test_eighteen_runs_of_four_lines_is_one_pick(session):
    """The 2026-09-26 shape exactly: 72 rows where there should be one."""
    lines = [_a(line=l, edge=e) for l, e in
             ((204.5, 12.0), (205.5, 11.0), (207.5, 10.0), (211.5, 8.0))]
    for _ in range(18):
        _store_prop_picks(session, lines, strategy_id=7)

    assert len(_props(session)) == 1


def test_the_stored_line_is_the_best_edge_whatever_the_order(session):
    """Storage itself must go through the line collapse. Without it the key
    still yields one row, but it is whichever line happened to come last."""
    added, _, _ = _store_prop_picks(session, [
        _a(line=204.5, edge=12.0), _a(line=211.5, edge=8.0),
        _a(line=207.5, edge=10.0)], strategy_id=7)

    (pick,) = _props(session)
    assert added == 1
    assert "204.5" in pick.pick_value


def test_a_rerun_refreshes_the_pick_in_place(session):
    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)
    first_id = _props(session)[0].id

    added, refreshed, _ = _store_prop_picks(
        session, [_a(line=207.5, odds=-105)], strategy_id=7)

    (pick,) = _props(session)
    assert (added, refreshed) == (0, 1)
    assert pick.id == first_id
    assert pick.odds_at_pick == -105
    assert "207.5" in pick.pick_value


def test_a_graded_pick_is_never_rewritten_or_duplicated(session):
    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)
    (pick,) = _props(session)
    session.add(PickResult(pick_id=pick.id, result="win", payout=0.91))
    session.commit()

    _store_prop_picks(session, [_a(line=207.5, odds=-105)], strategy_id=7)

    (after,) = _props(session)
    assert after.odds_at_pick == -110
    assert "205.5" in after.pick_value


def test_a_started_game_gets_no_new_prop_pick(session):
    """In-play prop prices are not takeable, the same rule as skip_started."""
    added, _, _ = _store_prop_picks(session, [_a(game_id=2)], strategy_id=7)

    assert added == 0
    assert _props(session) == []


def test_each_strategy_keeps_its_own_pick(session):
    _store_prop_picks(session, [_a()], strategy_id=7)
    _store_prop_picks(session, [_a()], strategy_id=8)

    assert len(_props(session)) == 2


def test_existing_duplicates_do_not_multiply_or_crash(session):
    """Rows already duplicated before this fix must not break a run."""
    for _ in range(3):
        session.add(PickModel(game_id=1, strategy_id=7, pick_type="prop",
                              pick_value="Julian Lewis Over 205.5 Pass Yards",
                              confidence=3, edge_pct=10.0, odds_at_pick=-110,
                              prop_player="Julian Lewis",
                              prop_market="player_pass_yds",
                              created_at=datetime.now(timezone.utc)))
    session.commit()

    added, refreshed, _ = _store_prop_picks(session, [_a()], strategy_id=7)

    assert added == 0 and refreshed == 1
    assert len(_props(session)) == 3
