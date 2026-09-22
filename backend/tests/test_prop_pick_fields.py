"""PickModel must be able to describe a prop well enough to grade it."""
import datetime

from backend.models import Base, Game, PickModel, StrategyModel, Team


def _seed_refs(session):
    """picks.game_id and picks.strategy_id are real foreign keys, and
    get_engine turns PRAGMA foreign_keys ON."""
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="Cleveland Cavaliers", abbreviation="CLE", sport="nba"),
        Team(id=2, name="New York Knicks", abbreviation="NY", sport="nba"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nba", season="2025-26",
                     date=datetime.date(2026, 5, 24), home_team_id=1, away_team_id=2,
                     home_score=110, away_score=105, status="final"))
    session.add(StrategyModel(id=1, name="props", config_json="{}",
                              strategy_type="prop"))
    session.commit()


def test_pick_model_carries_the_player_and_market_a_prop_needs(db_session):
    """`grade_prop_pick(pick_value, market, player_stat)` needs a market key and
    the player's box score.

    `pick_value` is prose -- "Dean Wade Over 0.5 3-Pointers" -- and the market
    cannot be recovered from it reliably: `prop_pipeline._market_label` maps
    only 7 of the 21 keys in `MARKET_STAT_MAP`, so for the other 14 the label IS
    the key and the reverse mapping is not one-to-one. Store both rather than
    re-parse.
    """
    _seed_refs(db_session)
    db_session.add(PickModel(
        game_id=1, strategy_id=1, pick_type="prop",
        pick_value="Dean Wade Over 0.5 3-Pointers",
        confidence=5, edge_pct=4.0, odds_at_pick=-200,
        prop_player="Dean Wade", prop_market="player_threes",
    ))
    db_session.commit()

    row = db_session.query(PickModel).one()
    assert row.prop_player == "Dean Wade"
    assert row.prop_market == "player_threes"


def test_the_fields_stay_empty_for_a_game_pick(db_session):
    """Only props carry them; a moneyline pick must not be forced to invent a
    player."""
    _seed_refs(db_session)
    db_session.add(PickModel(
        game_id=1, strategy_id=1, pick_type="moneyline",
        pick_value="HOME ML", confidence=3, edge_pct=2.0, odds_at_pick=-110,
    ))
    db_session.commit()

    row = db_session.query(PickModel).one()
    assert row.prop_player is None
    assert row.prop_market is None


# --- generated prop picks carry the fields -----------------------------------

from backend.data_types import PropAnalysis  # noqa: E402
from backend.pipeline.prop_pipeline import _build_prop_pick  # noqa: E402


def _analysis(player="Dean Wade", market="player_threes", line=0.5,
              outcome="Over", odds=-200):
    return PropAnalysis(
        player_name=player, market=market, line=line, outcome=outcome,
        season_avg=1.2, recent_avg=1.4, projection=1.3, edge_pct=12.0,
        confidence=5, source="nba_api", is_stale=False, game_id=1, odds=odds,
        bookmaker="draftkings",
    )


def test_a_generated_prop_pick_records_the_player_and_market_key():
    """Backfilling these later means reverse-engineering the market from a
    display label, which is lossy. Written at generation time they are exact.
    """
    pick = _build_prop_pick(_analysis(), strategy_id=7)

    assert pick.prop_player == "Dean Wade"
    assert pick.prop_market == "player_threes"      # the KEY, not "3-Pointers"
    assert pick.pick_value == "Dean Wade Over 0.5 3-Pointers"
    assert pick.pick_type == "prop"
    assert pick.strategy_id == 7
    assert pick.confidence == 5
    assert pick.odds_at_pick == -200


def test_the_stored_market_is_the_key_even_when_it_has_no_display_label():
    """`_market_label` covers 7 of 16 markets; the rest fall through as
    themselves. prop_market must be the key in both cases, so grading never
    has to know which kind it got."""
    pick = _build_prop_pick(_analysis(market="player_blocks"), strategy_id=7)

    assert pick.prop_market == "player_blocks"
    assert pick.pick_value.endswith("player_blocks")   # no label exists for it
