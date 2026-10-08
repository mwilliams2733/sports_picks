"""Read-only paper-trading quotes. Open like every GET: prices are public."""
from fastapi import APIRouter, HTTPException, Query, Request

from backend.database import get_session
from backend.models import Game
from backend.paper import board as board_mod
from backend.paper import pricing

router = APIRouter()


def _game_or_404(session, game_id: int):
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.get("/quotes")
def quotes(request: Request, game_id: int = Query(...)):
    """The six game-market sides for one game, each priced or refused."""
    session = get_session(request.app.state.engine)
    try:
        game = _game_or_404(session, game_id)
        return {"game_id": game.id, "quotes": pricing.game_quotes(session, game)}
    finally:
        session.close()


@router.get("/prop-quotes")
def prop_quotes(request: Request, game_id: int = Query(...)):
    """Every gradeable prop line a book quotes on one game, priced or refused."""
    session = get_session(request.app.state.engine)
    try:
        game = _game_or_404(session, game_id)
        return {"game_id": game.id, "quotes": pricing.prop_quotes(session, game)}
    finally:
        session.close()


@router.get("/board")
def board(request: Request, sport: str | None = None, days: int = 7):
    """Every game open for betting from today through ``days`` days ahead (ET,
    clamped 1..14), each side priced or refused exactly as a bet would be."""
    session = get_session(request.app.state.engine)
    try:
        return {"games": board_mod.build_board(session, sport=sport, days=days)}
    finally:
        session.close()
