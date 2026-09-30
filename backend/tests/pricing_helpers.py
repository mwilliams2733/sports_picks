"""Fresh book quotes for tests that place paper bets (plan 027).

Bets are priced by the server now, so a test game needs a quote less than
six hours old. Defaults: both moneylines -110, spread -3.5/+3.5 at -110,
total 220.5 at -110 -- so a HOME ML bet is charged -110, as the old tests
assumed.
"""
from datetime import datetime, timezone

from backend.database import get_session
from backend.models import Odds, PlayerProp

ODDS_DEFAULTS = dict(
    bookmaker="testbook", moneyline_home=-110, moneyline_away=-110,
    spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110,
    over_under=220.5, over_price=-110, under_price=-110,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_fresh_odds(engine, game_id: int, **fields) -> None:
    s = get_session(engine)
    s.add(Odds(game_id=game_id, timestamp=_now(), **{**ODDS_DEFAULTS, **fields}))
    s.commit()
    s.close()


def seed_fresh_prop(engine, game_id: int, **fields) -> None:
    defaults = dict(bookmaker="testbook", market="player_pass_yds",
                    player_name="QB One", outcome="Over", line=225.5, odds=-110)
    s = get_session(engine)
    s.add(PlayerProp(game_id=game_id, fetched_at=_now(), **{**defaults, **fields}))
    s.commit()
    s.close()
