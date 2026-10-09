from datetime import timedelta
from typing import Annotated, Literal, Union
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator
from sqlalchemy import func
from backend.api.auth import require_owner
from backend.api.pins import PIN_PATTERN, guard as pin_guard, hash_pin, require_player_pin
from backend.database import get_session
from backend.models import UserProfile, PaperPick, Game, ActivityFeed, Parlay
from backend.paper import bets as bets_mod
from backend.paper import pricing
from backend.paper.feed import log_feed_event as _log_feed_event, settle_and_announce
from backend.paper.pricing import PricingError
from backend.pipeline.team_stats import COMBAT_SPORTS
from backend.analysis.paper_bets import player_bets
from backend.analysis.scorecard import summarize, effective_bets
from backend.digest.record import emailed_bets
import json
import logging
from backend.api.picks import _resolve_pick_value
from backend.time_utils import as_utc, et_today, game_start_utc

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_pin(value: str) -> str:
    if not PIN_PATTERN.match(value):
        raise ValueError("PIN must be 4-6 digits")
    return value


class CreateUserRequest(BaseModel):
    name: str
    pin: str

    @field_validator("pin")
    @classmethod
    def _pin(cls, v: str) -> str:
        return _check_pin(v)


class SetPinRequest(BaseModel):
    pin: str

    @field_validator("pin")
    @classmethod
    def _pin(cls, v: str) -> str:
        return _check_pin(v)


class _Strict(BaseModel):
    # Unknown keys are refused, not ignored: an old client sending `odds` or
    # `pick_value` gets a 422 rather than a bet at a price it did not choose.
    model_config = ConfigDict(extra="forbid")


class GameLeg(_Strict):
    game_id: int
    pick_type: Literal["moneyline", "spread", "over_under"]
    side: Literal["HOME", "AWAY", "Over", "Under"]
    # Price-move protection (sportsbook spec §6): the price and line the
    # player saw. Present -> a different fresh quote is refused with 409
    # price_moved and nothing is written; absent -> priced as before (the
    # Claude-picks script sends neither).
    expected_odds: int | None = None
    expected_line: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _side_fits_market(self):
        allowed = ("Over", "Under") if self.pick_type == "over_under" else ("HOME", "AWAY")
        if self.side not in allowed:
            raise ValueError(f"side for {self.pick_type} must be {' or '.join(allowed)}")
        return self

    def to_bet(self) -> pricing.GameBet:
        return pricing.GameBet(self.game_id, self.pick_type, self.side)


class PropLeg(_Strict):
    game_id: int
    pick_type: Literal["prop"]
    prop_player: str = Field(min_length=1, max_length=100)
    prop_market: str = Field(min_length=1, max_length=64)
    outcome: Literal["Over", "Under"]
    line: float = Field(allow_inf_nan=False)
    # A prop's line is part of the bet itself, so only the price is checked;
    # an expected_line is refused (extra="forbid") rather than ignored.
    expected_odds: int | None = None

    def to_bet(self) -> pricing.PropBet:
        return pricing.PropBet(self.game_id, self.prop_player, self.prop_market,
                               self.outcome, self.line)


Leg = Annotated[Union[GameLeg, PropLeg], Field(discriminator="pick_type")]


class GameBetRequest(GameLeg):
    stake: float = Field(allow_inf_nan=False)


class PropBetRequest(PropLeg):
    stake: float = Field(allow_inf_nan=False)


class PlacePickRequest(RootModel[Annotated[Union[GameBetRequest, PropBetRequest],
                                           Field(discriminator="pick_type")]]):
    pass


class PlaceParlayRequest(_Strict):
    legs: list[Leg]
    stake: float = Field(allow_inf_nan=False)


def _priced(session, game, leg) -> pricing.Quote:
    """Price one bet or leg, turning a refusal into its HTTP status."""
    try:
        return pricing.price(session, game, leg.to_bet())
    except PricingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None


def _check_expected(leg, quote: pricing.Quote, index: int | None = None) -> None:
    """Refuse with 409 ``price_moved`` when the player saw a different price
    or line than the fresh quote. Only the fields the request sent are
    compared: an explicit ``expected_line: null`` (a moneyline) must find no
    line, and a request with neither field is priced as before."""
    moved = leg.expected_odds is not None and leg.expected_odds != quote.odds
    if "expected_line" in leg.model_fields_set and leg.expected_line != quote.line:
        moved = True
    if not moved:
        return
    detail = {"reason": "price_moved",
              "message": f"The price moved to {quote.pick_value} {quote.odds:+d}.",
              "odds": quote.odds, "line": quote.line, "pick_value": quote.pick_value}
    if index is not None:
        detail["leg"] = index
    raise HTTPException(status_code=409, detail=detail)


#: What a feed leg adds to the bet request for display; everything else in a
#: feed leg is exactly the request a friend's Tail sends back.
_FEED_DISPLAY_KEYS = ("label", "game_label", "start_time", "home_team", "away_team", "odds", "quoted_line")


def _feed_leg(leg, quote: pricing.Quote, game) -> dict:
    """A placed leg as the feed carries it (spec §10): the bet request itself,
    so Tail re-places it at today's price, plus what the feed shows."""
    home, away = game.home_team.abbreviation, game.away_team.abbreviation
    start = game_start_utc(game)
    request = leg.model_dump(exclude={"stake", "expected_odds", "expected_line"})
    label = quote.pick_value if quote.pick_type == "prop" else _resolve_pick_value(quote.pick_value, home, away)
    return {**request, "label": label, "game_label": f"{away} @ {home}",
            "start_time": start.isoformat() if start else None,
            "home_team": home, "away_team": away, "odds": quote.odds,
            # Not "line": a prop request already has a line (part of the bet).
            "quoted_line": quote.line}


def balance_of(session, user) -> float:
    """Starting balance plus every settled straight bet and parlay.

    Parlay legs are excluded (stake 0, payout 0); the parlay's own payout
    is on its Parlay row, which this used to leave out entirely.
    """
    straight = (session.query(func.coalesce(func.sum(PaperPick.payout), 0.0))
                .filter(PaperPick.user_id == user.id, PaperPick.parlay_id.is_(None))
                .scalar())
    parlays = (session.query(func.coalesce(func.sum(Parlay.payout), 0.0))
               .filter(Parlay.user_id == user.id).scalar())
    return user.starting_balance + straight + parlays


def open_stakes(session, user) -> float:
    """Stake riding on bets that have not settled yet.

    A straight bet's or parlay's stake only reaches `balance_of` as the
    payout when it settles, so until then it is in neither number. Parlay
    legs carry stake 0 and are excluded like they are there.
    """
    straight = (session.query(func.coalesce(func.sum(PaperPick.stake), 0.0))
                .filter(PaperPick.user_id == user.id, PaperPick.parlay_id.is_(None),
                        PaperPick.result.is_(None))
                .scalar())
    parlays = (session.query(func.coalesce(func.sum(Parlay.stake), 0.0))
               .filter(Parlay.user_id == user.id, Parlay.result.is_(None)).scalar())
    return straight + parlays


def hold_bankroll(session, user_id: int) -> None:
    """Take the database write lock before a bet reads what is available.

    Placing a bet reads `available_of` and then inserts, two steps another
    request from the same player can interleave (a double-click, two tabs;
    sync endpoints run in a threadpool): both read $10,000, both stake
    $6,000. A no-op UPDATE is the transaction's first write, so SQLite hands
    out its single write lock here -- a second bet waits (the driver's 5s
    busy timeout) until the first commits or rolls back, then reads a
    balance that includes it. The lock is database-wide, not per player:
    every other writer (other players' bets, the scheduler) waits too, for
    the few milliseconds a bet takes. It must come before the balance read: in WAL a
    transaction that has already read cannot then take the lock on a newer
    snapshot. The lock is released when the session commits or closes.
    """
    session.query(UserProfile).filter(UserProfile.id == user_id).update(
        {UserProfile.id: UserProfile.id}, synchronize_session=False)


def available_of(session, user) -> float:
    """What a player can still stake: settled balance minus open stakes.

    The bet checks used `balance_of` alone, so a $10,000 bankroll could
    place any number of $10,000 bets while the games were still to play.
    `balance_of` stays the settled number the leaderboard ranks on.
    """
    return balance_of(session, user) - open_stakes(session, user)


def _event_key(session, game_id: int):
    """What makes two parlay legs the same event.

    The game row, except in combat: one bout is sometimes stored as two
    games at different dates, with the two sources disagreeing about who
    is "home" ("Kape vs Van" at 2026-12-26 and 2027-06-30), and a parlay
    keyed on the row would price one fighter's win as two independent
    legs. A bout is its two fighters, in either order. Team sports keep
    the row: the same two teams on consecutive days are two real games.
    """
    game = session.get(Game, game_id)
    if game is not None and game.sport in COMBAT_SPORTS:
        return ("bout", game.sport,
                frozenset((game.home_team_id, game.away_team_id)))
    return game_id


def _open_for_betting(game) -> bool:
    """See backend.paper.pricing.open_for_betting -- the one definition."""
    return pricing.open_for_betting(game)


@router.get("/")
def list_users(request: Request):
    """List all user profiles with current balance and record."""
    session = get_session(request.app.state.engine)
    try:
        users = session.query(UserProfile).all()
        result = []
        for u in users:
            bets = player_bets(session, u.id)
            s = summarize(bets)
            total_wagered = sum(b.stake for b in bets)
            current_balance = balance_of(session, u)
            profit = round(current_balance - u.starting_balance, 2)
            result.append({
                "id": u.id,
                "name": u.name,
                "has_pin": u.pin_hash is not None,
                "starting_balance": u.starting_balance,
                "current_balance": round(current_balance, 2),
                "available_balance": round(available_of(session, u), 2),
                "total_wagered": round(total_wagered, 2),
                "profit": profit,
                "roi": round(s.roi * 100, 2) if s.roi is not None else 0,
                "wins": s.wins,
                "losses": s.losses,
                "pushes": s.pushes,
                "pending": s.pending,
                "win_rate": round(s.win_rate * 100, 1) if s.win_rate is not None else 0,
                "current_streak": u.current_streak or 0,
                "best_streak": u.best_streak or 0,
                "streak_type": u.streak_type or "none",
                "created_at": str(u.created_at),
            })
        # Sort by current_balance descending (leaderboard)
        result.sort(key=lambda x: x["current_balance"], reverse=True)
        return result
    finally:
        session.close()


MIN_RANKED_BETS = 10
#: Shrinkage toward the prior, in EFFECTIVE bets (backend.analysis.scorecard.
#: effective_bets), not raw count: a record of n_eff effective bets gets
#: weight n_eff / (n_eff + SHRINK_BETS). Chosen 2026-09-28 so a 7-3 start at
#: -110 ranks below a 40-30 record (brief G.2); revisit only with a dated note.
#:
#: Fix round 1 (2026-09-28): the raw bet count let one huge, volatile bet
#: (e.g. a $5,000 win at +200 among nine $10 losses) buy an outsized shrunk
#: ROI just by being one of ten "bets" -- effective_bets discounts it by how
#: much information it actually carries.
SHRINK_BETS = 50
#: The prior: ROI of picking sides at random at -110 (win half, pay the vig).
PRIOR_ROI = 0.5 * (100 / 110) - 0.5


def shrunk_roi(roi: float | None, n_eff: float) -> float | None:
    """ROI pulled toward PRIOR_ROI by SHRINK_BETS pseudo-bets, weighted by
    effective (not raw) bet count -- see effective_bets and SHRINK_BETS."""
    if roi is None:
        return None
    return (n_eff * roi + SHRINK_BETS * PRIOR_ROI) / (n_eff + SHRINK_BETS)


def _board_row(user_id, name, is_model, s, bets) -> dict:
    n_eff = effective_bets(bets)
    shrunk = shrunk_roi(s.roi, n_eff)
    return {"id": user_id, "name": name, "is_model": is_model,
            "wins": s.wins, "losses": s.losses, "pushes": s.pushes,
            "pending": s.pending, "n": s.n, "n_eff": round(n_eff, 2),
            "win_rate": None if s.win_rate is None else round(s.win_rate, 4),
            "roi": None if s.roi is None else round(s.roi, 4),
            "shrunk_roi": None if shrunk is None else round(shrunk, 4),
            "profit": round(s.profit, 2), "ranked": s.n >= MIN_RANKED_BETS}


def _board_order(row: dict):
    if row["ranked"]:
        return (0, -(row["shrunk_roi"] or 0.0), row["name"].lower())
    return (1, -row["n"], row["name"].lower())


@router.get("/leaderboard")
def leaderboard(request: Request):
    """Every player plus the Model (emailed picks, 1u each), ranked by shrunk ROI.

    ROI is profit / stake, so a $1,000 bettor and a 1u model compare fairly;
    shrinking it toward PRIOR_ROI (weighted by effective, not raw, bet count)
    keeps both a hot start and a single lucky whale bet from topping the
    board. Straight bets only. Fewer than MIN_RANKED_BETS settled bets:
    shown, not ranked -- ranking still uses the raw count, since a record
    needs enough bets shown at all before its effective size matters.
    """
    session = get_session(request.app.state.engine)
    try:
        rows = []
        for u in session.query(UserProfile).all():
            bets = player_bets(session, u.id, include_parlays=False)
            rows.append(_board_row(u.id, u.name, False, summarize(bets), bets))
        model_bets = emailed_bets(session, None)
        rows.append(_board_row(None, "Model", True, summarize(model_bets), model_bets))
        rows.sort(key=_board_order)
        return rows
    finally:
        session.close()


@router.post("/")
def create_user(request: Request, body: CreateUserRequest):
    """Create a new user profile."""
    session = get_session(request.app.state.engine)
    try:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name required")
        taken = session.query(UserProfile).filter(
            func.lower(func.trim(UserProfile.name)) == name.lower()).first()
        if taken:
            raise HTTPException(status_code=400, detail="Username already taken")
        pin_hash, pin_salt = hash_pin(body.pin)
        user = UserProfile(name=name, pin_hash=pin_hash, pin_salt=pin_salt)
        session.add(user)
        session.commit()
        return {"id": user.id, "name": user.name, "starting_balance": user.starting_balance}
    finally:
        session.close()


@router.get("/feed")
def get_activity_feed(request: Request, limit: int = Query(50, ge=1, le=200)):
    """Get recent activity feed events."""
    session = get_session(request.app.state.engine)
    try:
        events = (
            session.query(ActivityFeed)
            .order_by(ActivityFeed.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "user_id": e.user_id,
                "event_type": e.event_type,
                "payload": json.loads(e.payload),
                # Stored naive UTC; sent with its offset so a browser does not read it as local.
                "created_at": as_utc(e.created_at).isoformat() if e.created_at else None,
            }
            for e in events
        ]
    finally:
        session.close()


@router.delete("/{user_id}", dependencies=[Depends(require_owner)])
def delete_user(request: Request, user_id: int):
    """Delete a user and all their picks."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        # Order matters: foreign_keys=ON, and parlay legs reference Parlay.
        session.query(PaperPick).filter(PaperPick.user_id == user_id).delete()
        session.query(Parlay).filter(Parlay.user_id == user_id).delete()
        session.query(ActivityFeed).filter(ActivityFeed.user_id == user_id).delete()
        session.delete(user)
        session.commit()
        return {"deleted": True, "id": user_id}
    finally:
        session.close()


@router.put("/{user_id}/pin", dependencies=[Depends(require_owner)])
def reset_pin(request: Request, user_id: int, body: SetPinRequest):
    """Owner-only: set a player's PIN (a friend forgot theirs)."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.pin_hash, user.pin_salt = hash_pin(body.pin)
        session.commit()
        pin_guard.clear(user_id)
        return {"id": user_id, "pin_set": True}
    finally:
        session.close()


@router.get("/{user_id}")
def get_user(request: Request, user_id: int):
    """Get a user profile with full stats."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        bets = player_bets(session, user_id)
        s = summarize(bets)
        total_wagered = sum(b.stake for b in bets)
        current_balance = balance_of(session, user)
        profit = round(current_balance - user.starting_balance, 2)
        return {
            "id": user.id,
            "name": user.name,
            "has_pin": user.pin_hash is not None,
            "starting_balance": user.starting_balance,
            "current_balance": round(current_balance, 2),
            "available_balance": round(available_of(session, user), 2),
            "total_wagered": round(total_wagered, 2),
            "profit": profit,
            "roi": round(s.roi * 100, 2) if s.roi is not None else 0,
            "wins": s.wins,
            "losses": s.losses,
            "pushes": s.pushes,
            "pending": s.pending,
            "win_rate": round(s.win_rate * 100, 1) if s.win_rate is not None else 0,
        }
    finally:
        session.close()


@router.post("/{user_id}/picks", dependencies=[Depends(require_player_pin)])
def place_pick(request: Request, user_id: int, body: PlacePickRequest):
    """Place a paper pick for a user."""
    bet = body.root
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if bet.stake <= 0:
            raise HTTPException(status_code=400, detail="Stake must be positive")

        hold_bankroll(session, user_id)
        available = available_of(session, user)

        if bet.stake > available:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # Check if the game exists
        game = session.get(Game, bet.game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        if not _open_for_betting(game):
            raise HTTPException(status_code=400,
                                 detail="Betting has closed: this game has already started")

        quote = _priced(session, game, bet)
        _check_expected(bet, quote)

        pick = PaperPick(
            user_id=user_id,
            game_id=bet.game_id,
            pick_type=quote.pick_type,
            pick_value=quote.pick_value,
            odds=quote.odds,
            stake=bet.stake,
            result=None,
            payout=None,
            prop_market=quote.prop_market,
            prop_player=quote.prop_player,
        )
        session.add(pick)
        session.commit()

        # Log activity feed event
        loop = request.app.state.loop
        user = session.query(UserProfile).get(user_id)
        user_name = user.name if user else "Unknown"
        odds_str = f"{quote.odds:+d}" if quote.odds >= 0 else str(quote.odds)
        placed_leg = _feed_leg(bet, quote, game)
        _log_feed_event(session, loop, user_id, "pick_placed", {
            "user_name": user_name,
            "message": f"{user_name} bet {placed_leg['label']} {odds_str} — ${bet.stake:,.0f}",
            "pick_value": quote.pick_value,
            "odds": quote.odds,
            "stake": bet.stake,
            "bet_id": pick.id,
            "kind": "straight",
            "legs": [placed_leg],
        })

        return {
            "id": pick.id,
            "result": None,
            "payout": None,
            "new_balance": round(available - bet.stake, 2),
            **{k: v for k, v in quote.as_dict().items()
               if k in ("pick_value", "odds", "line", "quoted_at")},
        }
    finally:
        session.close()


@router.get("/{user_id}/picks")
def get_user_picks(request: Request, user_id: int):
    """Get all picks for a user."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        picks = (
            session.query(PaperPick, Game)
            .join(Game, PaperPick.game_id == Game.id)
            .filter(PaperPick.user_id == user_id)
            .order_by(PaperPick.created_at.desc())
            .all()
        )

        result = []
        for p, g in picks:
            entry = {
                "id": p.id,
                "game_id": p.game_id,
                "sport": g.sport,
                "date": str(g.date),
                "status": g.status,
                "pick_type": p.pick_type,
                "pick_value": p.pick_value,
                "odds": p.odds,
                "stake": p.stake,
                "result": p.result,
                "payout": p.payout,
                "created_at": str(p.created_at),
            }
            if p.prop_market:
                entry["prop_market"] = p.prop_market
            if p.prop_player:
                entry["prop_player"] = p.prop_player
            result.append(entry)
        return result
    finally:
        session.close()


@router.get("/{user_id}/bets")
def get_user_bets(request: Request, user_id: int):
    """A player's bets as tickets for My Bets (spec §7), with the money strip.

    Every figure is the one the rest of the app uses: available/balance/open
    stakes from the bankroll functions, Today's P/L from the same scorecard as
    /stats' "today". /{user_id}/picks keeps its flat shape for older callers.
    """
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        today = et_today()
        todays = [b for b in player_bets(session, user_id) if b.day == today]
        return {
            "summary": {
                "available": round(available_of(session, user), 2),
                "balance": round(balance_of(session, user), 2),
                "open_stakes": round(open_stakes(session, user), 2),
                "today_pl": _compute_period_stats(todays)["profit"],
            },
            "tickets": bets_mod.tickets(session, user_id),
        }
    finally:
        session.close()


@router.post("/{user_id}/parlay", dependencies=[Depends(require_player_pin)])
def place_parlay(request: Request, user_id: int, body: PlaceParlayRequest):
    """Place a parlay bet with multiple legs across any sports."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if len(body.legs) < 2:
            raise HTTPException(status_code=400, detail="Parlay requires at least 2 legs")
        if body.stake <= 0:
            raise HTTPException(status_code=400, detail="Stake must be positive")

        # Same-game parlays are allowed (owner ruling, fix round 2) -- a
        # HOME ML + Over total on one game are independent enough markets.
        # But two legs on the *same market* are not: combine() treats every
        # leg as independent, so a duplicate leg would multiply one outcome's
        # price (fix round 1's {game, moneyline HOME} x2 at +264 for what is
        # one -110 outcome), and opposite sides of the same market (HOME and
        # AWAY ML, Over and Under on the same prop) are the same outcome
        # priced twice, not two independent ones -- both are rejected. A
        # market is (game, pick_type) for a game leg, or (game, player,
        # market) for a prop leg -- the side/outcome is deliberately left out
        # of the key so both sides of one market collide.
        market_keys = [
            ("prop", _event_key(session, leg.game_id), leg.prop_player, leg.prop_market)
            if leg.pick_type == "prop"
            else ("game", _event_key(session, leg.game_id), leg.pick_type)
            for leg in body.legs
        ]
        if len(set(market_keys)) != len(market_keys):
            raise HTTPException(status_code=400,
                                 detail="A parlay can't have two legs on the same market.")

        # Check balance
        hold_bankroll(session, user_id)
        available = available_of(session, user)
        if body.stake > available:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # Price every leg before creating the Parlay, so a refusal writes nothing.
        quotes = []
        for i, leg in enumerate(body.legs):
            game = session.get(Game, leg.game_id)
            if not game:
                raise HTTPException(status_code=404, detail=f"Game {leg.game_id} not found")
            if not _open_for_betting(game):
                raise HTTPException(
                    status_code=400,
                    detail=f"Betting has closed: game {leg.game_id} has already started")
            quote = _priced(session, game, leg)
            _check_expected(leg, quote, i)
            quotes.append((leg, quote, game))

        combined_american, combined_decimal = pricing.combine([q.odds for _, q, _ in quotes])

        # Create parlay record
        parlay = Parlay(
            user_id=user_id,
            stake=body.stake,
            combined_odds=combined_american,
        )
        session.add(parlay)
        session.flush()

        # Create individual legs as PaperPick entries linked to this parlay
        leg_results = []
        for leg, quote, _ in quotes:
            session.add(PaperPick(
                user_id=user_id,
                game_id=leg.game_id,
                pick_type=quote.pick_type,
                pick_value=quote.pick_value,
                odds=quote.odds,
                stake=0,  # Individual legs have 0 stake; parlay has the stake
                result=None,
                payout=0,
                prop_market=quote.prop_market,
                prop_player=quote.prop_player,
                parlay_id=parlay.id,
            ))
            leg_results.append({"pick_value": quote.pick_value, "odds": quote.odds,
                                "line": quote.line,
                                "quoted_at": quote.quoted_at.isoformat(), "result": None})

        parlay_payout = None

        session.commit()

        # Log activity
        loop = request.app.state.loop
        user = session.query(UserProfile).get(user_id)
        user_name = user.name if user else "Unknown"
        feed_legs = [_feed_leg(leg, q, g) for leg, q, g in quotes]
        legs_str = " + ".join(fl["label"] for fl in feed_legs)
        _log_feed_event(session, loop, user_id, "pick_placed", {
            "user_name": user_name,
            "message": f"{user_name} placed {len(body.legs)}-leg parlay: {legs_str} — ${body.stake:,.0f} to win ${body.stake * (combined_decimal - 1):,.0f}",
            "parlay": True,
            "leg_count": len(body.legs),
            "bet_id": parlay.id,
            "kind": "parlay",
            "legs": feed_legs,
        })

        return {
            "id": parlay.id,
            "legs": leg_results,
            "combined_odds": combined_american,
            "potential_payout": round(body.stake * (combined_decimal - 1), 2),
            "result": parlay.result,
            "payout": parlay_payout,
            "new_balance": round(available - body.stake, 2),
        }
    finally:
        session.close()


@router.post("/grade", dependencies=[Depends(require_owner)])
def grade_paper_picks(request: Request):
    """Grade all pending paper picks for games that are final -- the same
    path the scheduler's automatic grading runs (backend/paper/feed.py)."""
    session = get_session(request.app.state.engine)
    try:
        done = settle_and_announce(session, request.app.state.loop)
        # The response keeps its shape (callers and tests read exactly these).
        return {"graded": done["graded"], "parlays_settled": done["parlays_settled"]}
    finally:
        session.close()


def _compute_period_stats(bets) -> dict:
    """Period stats for a player's Bets, through the shared scorecard."""
    s = summarize(bets)
    return {
        "wins": s.wins, "losses": s.losses, "pushes": s.pushes, "total": s.n,
        "win_rate": round(s.win_rate * 100, 1) if s.win_rate is not None else 0,
        "profit": round(s.profit, 2),
        "roi": round(s.roi * 100, 2) if s.roi is not None else 0,
    }


@router.get("/{user_id}/stats")
def get_user_stats(request: Request, user_id: int):
    """Get daily, weekly, monthly, and all-time stats for a user."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        bets = player_bets(session, user_id)
        today = et_today()
        week_start = today - timedelta(days=today.weekday())  # Monday
        month_start = today.replace(day=1)
        daily = [b for b in bets if b.day == today]
        weekly = [b for b in bets if b.day >= week_start]
        monthly = [b for b in bets if b.day >= month_start]

        # Daily breakdown for chart (last 30 days)
        daily_breakdown = []
        for i in range(30):
            d = today - timedelta(days=29 - i)
            day_picks = [b for b in bets if b.day == d and b.result is not None]
            if day_picks:
                stats = _compute_period_stats(day_picks)
                daily_breakdown.append({"date": str(d), **stats})

        return {
            "today": _compute_period_stats(daily),
            "this_week": _compute_period_stats(weekly),
            "this_month": _compute_period_stats(monthly),
            "all_time": _compute_period_stats(bets),
            "daily_breakdown": daily_breakdown,
        }
    finally:
        session.close()


