"""The pricing module is the only source of a paper bet's price (plan 027).

Expected prices are worked by hand from implied probabilities:
  -110 -> 110/210 = 0.523810   -130 -> 130/230 = 0.565217
  -120 -> 120/220 = 0.545455   +100 -> 100/200 = 0.500000
  +110 -> 100/210 = 0.476190
and back: p >= 0.5 -> -round(100p/(1-p)); p < 0.5 -> +round(100(1-p)/p).
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models import Base, Game, Odds, PlayerProp, Team
from backend.paper import pricing
from backend.paper.pricing import GameBet, PricingError, PropBet
from backend.pipeline.grader import grade_pick, grade_prop_pick

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)      # 11:00 ET
KICKOFF = datetime(2026, 10, 4, 17, 0)                         # naive UTC
GAME_DAY = date(2026, 10, 4)


@pytest.fixture
def session(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    return db_session


def _game(session, **overrides):
    home = Team(name="Kansas City Chiefs", abbreviation="Chiefs", sport="nfl")
    away = Team(name="Buffalo Bills", abbreviation="Bills", sport="nfl")
    session.add_all([home, away])
    session.flush()
    fields = dict(sport="nfl", season="2026", date=GAME_DAY,
                  home_team_id=home.id, away_team_id=away.id,
                  status="scheduled", start_time=KICKOFF)
    fields.update(overrides)
    game = Game(**fields)
    session.add(game)
    session.commit()
    return game


def _odds(session, game, book, age=timedelta(hours=1), **fields):
    session.add(Odds(game_id=game.id, bookmaker=book,
                     timestamp=(NOW - age).replace(tzinfo=None), **fields))
    session.commit()


def _prop(session, game, book, odds, age=timedelta(hours=1), player="Jalen Hurts",
          market="player_pass_yds", outcome="Over", line=225.5):
    session.add(PlayerProp(game_id=game.id, bookmaker=book, market=market,
                           player_name=player, outcome=outcome, line=line, odds=odds,
                           fetched_at=(NOW - age).replace(tzinfo=None)))
    session.commit()


def _price(session, game, bet):
    return pricing.price(session, game, bet, now=NOW)


# --- consensus -------------------------------------------------------------

def test_moneyline_is_the_consensus_of_two_books(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    _odds(session, g, "fd", moneyline_home=-130, moneyline_away=110)
    home = _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    away = _price(session, g, GameBet(g.id, "moneyline", "AWAY"))
    # home: (0.523810 + 0.565217)/2 = 0.544513 -> -round(119.55) = -120
    assert (home.odds, home.pick_value, home.line) == (-120, "HOME ML", None)
    # away: (0.500000 + 0.476190)/2 = 0.488095 -> +round(104.88) = +105
    assert (away.odds, away.pick_value) == (105, "AWAY ML")


def test_three_books(session):
    g = _game(session)
    for book, price in (("a", -110), ("b", -110), ("c", -120)):
        _odds(session, g, book, moneyline_home=price, moneyline_away=100)
    # (0.523810 + 0.523810 + 0.545455)/3 = 0.531025 -> -round(113.23) = -113
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -113


def test_a_stale_moneyline_book_does_not_pull_the_price(session):
    g = _game(session)
    _odds(session, g, "stale", age=timedelta(hours=7), moneyline_home=-300, moneyline_away=250)
    _odds(session, g, "fresh", age=timedelta(hours=1), moneyline_home=-110, moneyline_away=100)
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -110


def test_spread_uses_the_consensus_line_and_price(session):
    """Review Focus 3: books at -3 and -4 -> the bet is at -3.5."""
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.0, spread_away=3.0,
          spread_home_price=-110, spread_away_price=100)
    _odds(session, g, "fd", moneyline_home=-150, moneyline_away=130,
          spread_home=-4.0, spread_away=4.0,
          spread_home_price=-130, spread_away_price=110)
    home = _price(session, g, GameBet(g.id, "spread", "HOME"))
    away = _price(session, g, GameBet(g.id, "spread", "AWAY"))
    assert (home.pick_value, home.line, home.odds) == ("HOME -3.5", -3.5, -120)
    assert (away.pick_value, away.line, away.odds) == ("AWAY +3.5", 3.5, 105)


def test_total_uses_the_consensus_line_and_price(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          over_under=44.5, over_price=-110, under_price=-110)
    over = _price(session, g, GameBet(g.id, "over_under", "Over"))
    under = _price(session, g, GameBet(g.id, "over_under", "Under"))
    assert (over.pick_value, over.line, over.odds) == ("Over 44.5", 44.5, -110)
    assert under.pick_value == "Under 44.5"


# --- refusals ----------------------------------------------------------------

def test_an_unquoted_market_is_refused_and_never_priced_at_minus_110(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.5, spread_away=3.5)   # lines, but no spread prices
    for bet in (GameBet(g.id, "spread", "HOME"), GameBet(g.id, "over_under", "Over")):
        with pytest.raises(PricingError) as err:
            _price(session, g, bet)
        assert (err.value.reason, err.value.status) == ("not_quoted", 409)


def test_a_game_with_no_odds_rows_is_not_quoted(session):
    g = _game(session)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert err.value.reason == "not_quoted"


def test_an_unusable_book_price_is_ignored(session):
    """Review Focus 2: a 0 price neither prices the bet nor crashes."""
    g = _game(session)
    _odds(session, g, "bad", moneyline_home=0, moneyline_away=0)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert err.value.reason == "not_quoted"

    _odds(session, g, "dk", age=timedelta(hours=2), moneyline_home=-110, moneyline_away=100)
    quote = _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert quote.odds == -110
    # The bad row is newer (1h) but must not set the clock: 2h is the newest usable.
    assert quote.quoted_at == NOW - timedelta(hours=2)


@pytest.mark.parametrize("age, ok", [
    (timedelta(hours=5, minutes=59), True),
    (timedelta(hours=6, minutes=1), False),
])
def test_the_freshness_boundary(session, age, ok):
    g = _game(session)
    _odds(session, g, "dk", age=age, moneyline_home=-110, moneyline_away=100)
    bet = GameBet(g.id, "moneyline", "HOME")
    if ok:
        assert _price(session, g, bet).odds == -110
    else:
        with pytest.raises(PricingError) as err:
            _price(session, g, bet)
        assert (err.value.reason, err.value.status) == ("stale", 409)
        assert err.value.message == "The price is stale — ask Marcus to refresh."


def test_freshness_is_judged_per_market(session):
    g = _game(session)
    _odds(session, g, "fresh", age=timedelta(hours=1), moneyline_home=-110, moneyline_away=100)
    _odds(session, g, "old", age=timedelta(hours=7), moneyline_home=-110, moneyline_away=100,
          spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110)
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -110
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "spread", "HOME"))
    assert err.value.reason == "stale"


def test_a_stale_book_does_not_pull_the_line_or_price(session):
    """Review finding: Odds rows are upserted per book and never deleted, so
    a book that dropped out of the feed keeps its last row forever. A stale
    row must not move a market a fresh book is already quoting."""
    g = _game(session)
    _odds(session, g, "stale", age=timedelta(hours=7), moneyline_home=-110, moneyline_away=100,
          spread_home=-3.0, spread_away=3.0, spread_home_price=-110, spread_away_price=-110)
    _odds(session, g, "fresh", age=timedelta(hours=1), moneyline_home=-110, moneyline_away=100,
          spread_home=-7.0, spread_away=7.0, spread_home_price=-110, spread_away_price=-110)
    away = _price(session, g, GameBet(g.id, "spread", "AWAY"))
    assert (away.pick_value, away.line, away.odds) == ("AWAY +7", 7.0, -110)
    assert away.quoted_at == NOW - timedelta(hours=1)


def test_a_line_without_a_price_does_not_count_toward_the_line(session):
    """A row with the spread's line but no spread price never quoted this
    bet -- it must not out-vote the line the priced row actually carries."""
    g = _game(session)
    _odds(session, g, "line_only", moneyline_home=-110, moneyline_away=100,
          spread_home=-10.0, spread_away=10.0)   # no spread_home_price/spread_away_price
    _odds(session, g, "priced", moneyline_home=-110, moneyline_away=100,
          spread_home=-3.0, spread_away=3.0, spread_home_price=-110, spread_away_price=-110)
    home = _price(session, g, GameBet(g.id, "spread", "HOME"))
    assert home.line == -3.0


@pytest.mark.parametrize("overrides", [
    {"start_time": datetime(2026, 10, 4, 14, 0)},                  # kicked off an hour ago
    {"status": "final"},
    {"status": "in_progress"},
    {"start_time": None, "date": date(2026, 10, 3)},               # stale row from yesterday
])
def test_a_game_that_is_not_open_is_refused(session, overrides):
    g = _game(session, **overrides)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert (err.value.reason, err.value.status) == ("game_started", 400)


def test_a_same_day_game_with_no_start_time_is_open(session):
    g = _game(session, start_time=None)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -110


# --- props -------------------------------------------------------------------

def test_a_prop_is_the_consensus_of_the_books_quoting_that_exact_line(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    _prop(session, g, "fd", -130)
    _prop(session, g, "mgm", -200, line=226.5)       # a different line: not included
    q = _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    assert q.odds == -120                             # same arithmetic as the moneyline test
    assert q.pick_value == "Jalen Hurts Over 225.5 Pass Yards"
    assert (q.pick_type, q.line, q.prop_player, q.prop_market) == (
        "prop", 225.5, "Jalen Hurts", "player_pass_yds")


def test_an_unquoted_prop_line_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 230.5))
    assert err.value.reason == "not_quoted"


def test_an_ungradeable_prop_market_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110, player="Aaron Judge", market="batter_hits", line=0.5)
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Aaron Judge", "batter_hits", "Over", 0.5))
    assert (err.value.reason, err.value.status) == ("not_gradeable", 409)


def test_a_stale_prop_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110, age=timedelta(hours=6, minutes=1))
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    assert err.value.reason == "stale"


def test_a_stale_prop_book_does_not_pull_the_price(session):
    g = _game(session)
    _prop(session, g, "stale", -300, age=timedelta(hours=7))
    _prop(session, g, "fresh", -110, age=timedelta(hours=1))
    q = _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    assert q.odds == -110


def test_prop_quotes_lists_only_gradeable_over_under_lines(session):
    """Review Focus 1: anytime-TD rows (outcome Yes, no line) are left out."""
    g = _game(session)
    _prop(session, g, "dk", -110)
    _prop(session, g, "dk", -110, outcome="Under")
    _prop(session, g, "dk", 150, player="A.J. Brown", market="player_anytime_td",
          outcome="Yes", line=None)
    _prop(session, g, "dk", -110, player="Aaron Judge", market="batter_hits", line=0.5)
    rows = pricing.prop_quotes(session, g, now=NOW)
    assert [(r["prop_player"], r["outcome"], r["available"]) for r in rows] == [
        ("Jalen Hurts", "Over", True), ("Jalen Hurts", "Under", True)]
    assert rows[0]["market_label"] == "Pass Yards"
    assert rows[0]["odds"] == -110


def test_game_quotes_covers_six_sides_with_reasons(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    rows = pricing.game_quotes(session, g, now=NOW)
    assert [(r["pick_type"], r["side"], r["available"]) for r in rows] == [
        ("moneyline", "HOME", True), ("moneyline", "AWAY", True),
        ("spread", "HOME", False), ("spread", "AWAY", False),
        ("over_under", "Over", False), ("over_under", "Under", False)]
    assert rows[2]["reason"] == "not_quoted" and rows[2]["message"]
    assert rows[0]["quoted_at"] == (NOW - timedelta(hours=1)).isoformat()


# --- parlay ------------------------------------------------------------------

def test_combine_two_minus_110_legs():
    # (1 + 100/110)^2 = 1.909091^2 = 3.644628 -> +round(264.46) = +264
    american, decimal = pricing.combine([-110, -110])
    assert american == 264
    assert decimal == pytest.approx(3.644628, abs=1e-6)


def test_combine_short_parlay_stays_negative():
    # (1 + 100/400) * (1 + 100/500) = 1.25 * 1.2 = 1.5 -> -round(100/0.5) = -200
    assert pricing.combine([-400, -500])[0] == -200


def test_combine_empty_list_raises():
    with pytest.raises(ValueError):
        pricing.combine([])


# --- round trip: every label the module writes, the grader reads ------------

def test_every_game_label_grades_correctly(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110,
          over_under=44.5, over_price=-110, under_price=-110)
    expected = {  # final 27-20: home wins by 7, total 47
        ("moneyline", "HOME"): "win", ("moneyline", "AWAY"): "loss",
        ("spread", "HOME"): "win", ("spread", "AWAY"): "loss",
        ("over_under", "Over"): "win", ("over_under", "Under"): "loss",
    }
    for (pick_type, side), result in expected.items():
        q = _price(session, g, GameBet(g.id, pick_type, side))
        graded = grade_pick(q.pick_type, q.pick_value, 27, 20, q.odds)
        assert graded is not None and graded[0] == result, (q.pick_value, graded)


def test_a_prop_label_grades_correctly(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    q = _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    stat = SimpleNamespace(pass_yards=240.0)
    assert grade_prop_pick(q.pick_value, q.prop_market, stat)[0] == "win"
