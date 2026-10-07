"""The digest names the best book for each emailed pick (2026-10-07).

Measured by `backend.scripts.price_shopping_experiment`: the best of our 11
books' prices is worth about 3.7 points more at the close than a typical
book's on an NFL moneyline.
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine

from backend.analysis.best_price import best_game_price, best_prop_price, book_name
from backend.database import get_session, run_migrations
from backend.digest.record import record_emailed
from backend.digest.render import _price
from backend.digest.selector import DigestPick, DigestSection
from backend.models import EmailedPick, Game, PickModel, Team

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def _odds(book, **kw):
    base = dict(bookmaker=book, moneyline_home=None, moneyline_away=None, spread_home=None,
                spread_away=None, spread_home_price=None, spread_away_price=None,
                over_under=None, over_price=None, under_price=None)
    return NS(**{**base, **kw})


def test_moneyline_takes_the_highest_payout_for_the_side():
    rows = [_odds("draftkings", moneyline_home=135, moneyline_away=-160),
            _odds("fanduel", moneyline_home=150, moneyline_away=-175),
            _odds("betmgm", moneyline_home=140, moneyline_away=-155)]
    assert best_game_price(rows, "moneyline", "HOME ML") == best_game_price(rows, "moneyline", "HOME ML")
    assert best_game_price(rows, "moneyline", "HOME ML").bookmaker == "fanduel"
    assert best_game_price(rows, "moneyline", "AWAY ML").odds == -155


def test_the_nflverse_close_row_is_not_a_book():
    rows = [_odds("nflverse_close", moneyline_home=300), _odds("draftkings", moneyline_home=135)]
    assert best_game_price(rows, "moneyline", "HOME ML").bookmaker == "draftkings"


def test_spreads_and_totals_only_at_the_same_number():
    rows = [_odds("a", spread_home=-3.5, spread_home_price=-105, over_under=44.5, under_price=-110),
            _odds("b", spread_home=-3.0, spread_home_price=-130, over_under=45.0, under_price=+120),
            _odds("c", spread_home=-3.5, spread_home_price=-112, over_under=44.5, under_price=-105)]
    assert best_game_price(rows, "spread", "HOME -3.5").bookmaker == "a"
    assert best_game_price(rows, "over_under", "Under 44.5").bookmaker == "c"
    assert best_game_price(rows, "over_under", "Under 44.8") is None    # consensus number nobody quotes


def test_ties_go_to_the_first_book_by_name_whatever_the_order():
    rows = [_odds("fanduel", moneyline_home=150), _odds("betmgm", moneyline_home=150)]
    assert best_game_price(rows, "moneyline", "HOME ML").bookmaker == "betmgm"
    assert best_game_price(rows[::-1], "moneyline", "HOME ML").bookmaker == "betmgm"


def test_props_at_the_exact_player_market_side_and_line():
    rows = [NS(player_name="Josh Downs", market="player_reception_yds", outcome="Over", line=69.5,
               bookmaker="fanduel", odds=-114),
            NS(player_name="Josh Downs", market="player_reception_yds", outcome="Over", line=69.5,
               bookmaker="draftkings", odds=-120),
            NS(player_name="Josh Downs", market="player_reception_yds", outcome="Over", line=70.5,
               bookmaker="betmgm", odds=+100),                                     # other line
            NS(player_name="Josh Downs", market="player_reception_yds", outcome="Under", line=69.5,
               bookmaker="betus", odds=+105)]                                      # other side
    best = best_prop_price(rows, "Josh Downs", "player_reception_yds",
                           "Josh Downs Over 69.5 Receiving Yards")
    assert (best.bookmaker, best.odds) == ("fanduel", -114)
    assert best_prop_price(rows, "Josh Downs", "player_anytime_td", "Josh Downs Yes") is None


def _pick(**kw):
    base = dict(sport="nfl", matchup="A @ B", pick_value="HOME ML", odds=141, confidence=1,
                edge_pct=4.0, rationale="")
    return DigestPick(**{**base, **kw})


def test_the_email_shows_the_best_book_beside_the_average():
    assert _price(_pick(best_book="fanduel", best_odds=150)) == "(+141 avg · +150 at FanDuel)"
    assert _price(_pick(best_book="fanduel", best_odds=141)) == "(+141 at FanDuel)"
    assert _price(_pick()) == "(+141)"
    # The market moved against the pick after it was priced: never call a
    # worse price the best one.
    assert _price(_pick(odds=139, best_book="betonlineag", best_odds=134)) ==         "(+139 when picked · best now +134 at BetOnline)"
    assert book_name("williamhill_us") == "Caesars"


def test_the_emailed_record_keeps_what_readers_were_shown():
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.add(Game(id=1, sport="nfl", season="2026-27", date=date(2026, 10, 11), status="scheduled",
               home_team_id=1, away_team_id=2))
    s.add(PickModel(id=5, game_id=1, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                    confidence=1, edge_pct=4.0, odds_at_pick=141, created_at=NOW))
    s.commit()
    section = DigestSection(sport="nfl", props=[],
                            picks=[_pick(pick_id=5, best_book="fanduel", best_odds=150)])
    record_emailed(s, [section], date(2026, 10, 11))
    row = s.query(EmailedPick).one()
    assert (row.odds, row.best_book, row.best_odds) == (141, "fanduel", 150)


def test_the_selector_fills_the_best_book_from_the_stored_quotes():
    from backend.database import get_engine
    from backend.digest.selector import select_digest
    from backend.models import Base, Odds, PlayerProp, StrategyModel
    s = get_session(get_engine(":memory:"))
    Base.metadata.create_all(s.get_bind())
    d = date(2026, 11, 1)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=d, home_team_id=1, away_team_id=2,
               status="scheduled"))
    s.flush()      # foreign keys are enforced: the game must exist before its quotes
    s.add(PickModel(game_id=1, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                    confidence=1, edge_pct=4.0, odds_at_pick=141))
    s.add(PickModel(game_id=1, strategy_id=1, pick_type="prop", prop_player="Josh Downs",
                    prop_market="player_reception_yds",
                    pick_value="Josh Downs Over 69.5 Receiving Yards",
                    confidence=1, edge_pct=6.0, odds_at_pick=-114))
    s.add_all([Odds(game_id=1, bookmaker="draftkings", moneyline_home=135, moneyline_away=-160),
               Odds(game_id=1, bookmaker="fanduel", moneyline_home=150, moneyline_away=-175)])
    s.add_all([PlayerProp(game_id=1, bookmaker=b, market="player_reception_yds",
                          player_name="Josh Downs", outcome="Over", line=69.5, odds=o, fetched_at=NOW)
               for b, o in (("betmgm", -114), ("draftkings", -125))])
    s.commit()
    seasons = {"nfl": {"start": "09-05", "end": "02-10"}}
    bar = {"min_odds": -100_000, "max_odds": 100_000, "max_game_picks": 10, "max_props": 10,
           "blend_weight": {}}

    (section,) = select_digest(s, d, ["nfl"], seasons, send_bar=bar)

    assert (section.picks[0].best_book, section.picks[0].best_odds) == ("fanduel", 150)
    assert (section.props[0].best_book, section.props[0].best_odds) == ("betmgm", -114)


def test_the_migration_adds_the_columns_to_an_existing_table():
    """The live table predates the columns; create_all never alters it."""
    from sqlalchemy import inspect, text
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE emailed_picks (id INTEGER PRIMARY KEY, digest_date DATE, "
                          "pick_id INTEGER, game_id INTEGER, sport VARCHAR, pick_type VARCHAR, "
                          "pick_value VARCHAR, odds INTEGER, prop_player VARCHAR, "
                          "prop_market VARCHAR, confidence INTEGER, sent_at DATETIME)"))
    run_migrations(engine)
    run_migrations(engine)                                   # idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("emailed_picks")}
    assert {"best_book", "best_odds"} <= cols
