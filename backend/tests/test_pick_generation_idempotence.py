"""Generating picks twice for one day must not double the day's book.

`generate_and_store_picks` always inserted. Every window run re-picked every
scheduled game, so a day with three runs carried three copies of each pick.
Measured 2026-09-19: 354 picks for the day, 221 of them redundant copies.

Ungraded they are merely noise. Graded they are worse than noise -- the same
wager counted three times, tripling its weight in ROI.

It also had no sport filter, so a caller asking for one sport re-picked
every other sport as a side effect.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Odds, PickModel, StrategyModel, Team
from backend.pipeline.pick_generator import generate_and_store_picks
import backend.analysis.variants.ensemble as ens


@pytest.fixture(autouse=True)
def _markets_enabled(monkeypatch):
    """Treat these fixtures' sports as validated for spreads.

    SPREAD_VALIDATED_SPORTS is empty in production because the margin model
    loses to the market line. This module is about idempotence and refresh
    mechanics, and without a second market most of its fixtures would
    produce only a moneyline -- or nothing -- and stop testing what they
    claim to. test_spread_gate.py owns the gate decision.
    """
    monkeypatch.setattr(ens, "SPREAD_VALIDATED_SPORTS",
                        frozenset({"mlb", "ncaaf", "nba"}))

from backend.time_utils import et_today

TODAY = et_today()


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "p.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([
        Team(id=1, name="MH", abbreviation="MH", sport="mlb"),
        Team(id=2, name="MA", abbreviation="MA", sport="mlb"),
        Team(id=3, name="FH", abbreviation="FH", sport="ncaaf"),
        Team(id=4, name="FA", abbreviation="FA", sport="ncaaf"),
    ])
    s.flush()
    s.add_all([
        Game(id=1, sport="mlb", season="2026", date=TODAY, status="scheduled",
             home_team_id=1, away_team_id=2),
        Game(id=2, sport="ncaaf", season="2026", date=TODAY, status="scheduled",
             home_team_id=3, away_team_id=4),
    ])
    s.add_all([
        Odds(game_id=1, bookmaker="bk", moneyline_home=-150, moneyline_away=130,
             spread_home=-1.5, spread_away=1.5, over_under=8.5),
        Odds(game_id=2, bookmaker="bk", moneyline_home=-150, moneyline_away=130,
             spread_home=-3.5, spread_away=3.5, over_under=52.5),
    ])
    s.add(StrategyModel(id=1, name="ensemble",
                        # See test_max_edge_ceiling.py; these
                        # fixtures use large synthetic edges.
                        config_json='{"max_edge": 100.0}',
                        is_active=True, strategy_type="game"))
    s.commit()
    return s


def _picks(session, sport=None):
    q = session.query(PickModel).join(Game, Game.id == PickModel.game_id)
    if sport:
        q = q.filter(Game.sport == sport)
    return q.all()


def test_running_twice_does_not_duplicate_the_days_picks(session):
    """The regression. Three runs used to mean three copies of each pick."""
    generate_and_store_picks(session, 1, TODAY)
    first = len(_picks(session))
    generate_and_store_picks(session, 1, TODAY)

    assert len(_picks(session)) == first, "a second run duplicated the book"


def test_the_second_run_reports_only_what_it_added(session):
    generate_and_store_picks(session, 1, TODAY)
    assert generate_and_store_picks(session, 1, TODAY) == 0


def test_a_pick_on_an_unstarted_game_follows_the_market(session):
    """An unstarted game's pick is advice, so it tracks the current price.

    This deliberately replaced an earlier rule that `odds_at_pick` was
    frozen on first write. That rule protected ROI from being restated, but
    it also froze picks made on broken inputs: on 2026-09-20 a run wrote 17
    NFL picks with flat Elo, and the next scout recomputed them correctly
    and discarded every result because the markets were already picked. One
    had the wrong team.

    The price is only history once a bet could have been placed on it. Until
    the game starts nothing is takeable, so the honest stored price is the
    current one. `test_pick_refresh.py` holds the other half: a graded pick,
    and one whose game has started, are never rewritten -- and those are the
    cases ROI is actually measured against.
    """
    generate_and_store_picks(session, 1, TODAY)
    before = {p.id: p.odds_at_pick for p in _picks(session)}
    assert before, "fixture produced no picks"

    for o in session.query(Odds).all():      # the market moves
        # Both sides stay inside DEFAULT_MAX_ODDS: a moved price beyond
        # the longshot ceiling is refused rather than refreshed, which
        # would make this test about the ceiling instead of the refresh.
        o.moneyline_home, o.moneyline_away = -400, 190
    session.commit()
    generate_and_store_picks(session, 1, TODAY)

    after = {p.id: p.odds_at_pick for p in _picks(session)}
    assert set(after) >= set(before), "a refresh must not drop a pick row"
    assert any(after[k] != before[k] for k in before), (
        "an unstarted game's pick should have followed the market")
    # Only the picks the model still makes get refreshed, and only
    # moneylines carry the quote that moved -- spreads and totals are priced
    # at -110 and would not change here.
    changed = {k for k in before if after[k] != before[k]}
    assert changed, "nothing followed the market"
    assert all(after[k] in (-400, 190) for k in changed), (
        f"a refreshed price should be the current quote: "
        f"{ {k: after[k] for k in changed} }")


def test_a_pick_the_model_would_no_longer_make_is_left_standing(session):
    """A known gap, asserted so it is visible rather than surprising.

    Refresh replaces a pick with the freshly computed one for the same
    market. When the market moves far enough that the model produces no pick
    there at all, there is nothing to replace it with, so the original stays
    -- at its original price, still presented as current advice.

    Removing it would be the other reasonable choice. It is not made here
    because a pick that vanishes between runs is harder to reason about than
    one that goes stale, and nothing yet measures which costs more.
    """
    generate_and_store_picks(session, 1, TODAY)
    before = {p.id: (p.pick_value, p.odds_at_pick) for p in _picks(session)}

    for o in session.query(Odds).all():
        # Both sides stay inside DEFAULT_MAX_ODDS: a moved price beyond
        # the longshot ceiling is refused rather than refreshed, which
        # would make this test about the ceiling instead of the refresh.
        o.moneyline_home, o.moneyline_away = -400, 190
    session.commit()
    generate_and_store_picks(session, 1, TODAY)

    after = {p.id: (p.pick_value, p.odds_at_pick) for p in _picks(session)}
    assert set(after) >= set(before), "no pick is dropped by a refresh"
    assert any(after[k] == before[k] for k in before), (
        "at least one pick the model no longer makes should be untouched")


def test_a_sport_filter_leaves_other_sports_alone(session):
    """Asking for mlb must not re-pick ncaaf as a side effect."""
    generate_and_store_picks(session, 1, TODAY, sports=("mlb",))

    assert _picks(session, "mlb")
    assert not _picks(session, "ncaaf")


def test_without_a_filter_every_sport_is_picked(session):
    generate_and_store_picks(session, 1, TODAY)
    assert _picks(session, "mlb") and _picks(session, "ncaaf")


def test_a_deleted_pick_is_regenerated(session):
    """Deleting a bad pick and re-running must actually replace it.

    This is how a pick made on wrong inputs -- a neutral pitcher score, a
    missing price -- gets corrected.
    """
    generate_and_store_picks(session, 1, TODAY, sports=("mlb",))
    for p in _picks(session, "mlb"):
        session.delete(p)
    session.commit()

    added = generate_and_store_picks(session, 1, TODAY, sports=("mlb",))
    assert added > 0
    assert _picks(session, "mlb")


# --------------------------------------------------------------------------
# A pick must be placeable.
#
# Games keep status='scheduled' until ESPN reports otherwise, which lags by
# hours, and the odds feed switches to in-play prices the moment a game
# starts. Generating on 2026-09-19 at 23:57 UTC produced a moneyline pick at
# +3300 on a game that had started at 20:10 -- a price no one could take, on
# a result already half-decided, which would then be graded as a real wager.
# 20 of 34 mlb picks that run were on games already underway.
# --------------------------------------------------------------------------

def _set_start(session, gid, hours_ago):
    """Move the fixture's mlb game to a start_time relative to now.

    The game is edited rather than replaced: its Odds row references it, and
    deleting it trips the foreign key.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    game = session.get(Game, gid)
    game.start_time = now - datetime.timedelta(hours=hours_ago)
    session.commit()


def test_a_game_already_underway_is_not_picked(session):
    _set_start(session, 1, hours_ago=4)

    generate_and_store_picks(session, 1, TODAY, sports=("mlb",))

    assert not _picks(session, "mlb"), "picked a game that had already started"


def test_a_game_yet_to_start_is_still_picked(session):
    _set_start(session, 1, hours_ago=-3)     # starts in 3 hours

    generate_and_store_picks(session, 1, TODAY, sports=("mlb",))

    assert _picks(session, "mlb")


def test_a_game_with_no_start_time_is_still_picked(session):
    """Unknown is not the same as past; game 1 in the fixture has none."""
    generate_and_store_picks(session, 1, TODAY, sports=("mlb",))
    assert _picks(session, "mlb")


def test_backfills_can_opt_out(session):
    """Backtests deliberately pick games that are long over."""
    _set_start(session, 1, hours_ago=4)

    generate_and_store_picks(session, 1, TODAY, sports=("mlb",),
                             skip_started=False)

    assert _picks(session, "mlb")
