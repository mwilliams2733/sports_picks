"""A player's paper bets as scorecard Bets.

Straight bets once each, and each parlay once at its own stake and payout;
parlay legs (stake 0) never count, or a 3-leg parlay would be 3 bets.
"""
from sqlalchemy import func

from backend.analysis.scorecard import Bet
from backend.models import Game, PaperPick, Parlay


def player_bets(session, user_id: int, include_parlays: bool = True) -> list[Bet]:
    """include_parlays=False is the leaderboard's view: parlays multiply the
    vig, so they are kept off the board (brief G.3) but stay in a player's
    own stats and balance."""
    bets: list[Bet] = []
    straight = (session.query(PaperPick, Game).join(Game, Game.id == PaperPick.game_id)
                .filter(PaperPick.user_id == user_id, PaperPick.parlay_id.is_(None))
                .all())
    for pick, game in straight:
        bets.append(Bet(result=pick.result, stake=pick.stake,
                        profit=(pick.payout or 0.0) if pick.result else 0.0,
                        odds=pick.odds, day=game.date))
    parlays = (session.query(Parlay).filter(Parlay.user_id == user_id).all()
               if include_parlays else [])
    for parlay in parlays:
        last_leg = (session.query(func.max(Game.date))
                    .join(PaperPick, PaperPick.game_id == Game.id)
                    .filter(PaperPick.parlay_id == parlay.id).scalar())
        day = last_leg or parlay.created_at.date()
        bets.append(Bet(result=parlay.result, stake=parlay.stake,
                        profit=(parlay.payout or 0.0) if parlay.result else 0.0,
                        odds=parlay.combined_odds, day=day))
    return sorted(bets, key=lambda b: b.day)
