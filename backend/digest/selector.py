"""Choose and rank what goes into the daily digest.

Pure with respect to time and network: the caller supplies the target date
and a session. No sending, no formatting.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.config import is_sport_in_season
from backend.data_types import PickFactor
from backend.analysis.rationale import render_rationale
from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.analysis.prop_markets import MARKET_STAT_MAP
from backend.models import Game, PickModel, PickResult, Team

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DigestPick:
    sport: str
    matchup: str
    pick_value: str
    odds: int
    confidence: int
    edge_pct: float
    rationale: str
    model_prob: float | None = None
    price_prob: float | None = None
    home_team: str | None = None
    away_team: str | None = None
    #: The stored pick this was rendered from, so the send can be recorded
    #: (EmailedPick). None only for a DigestPick built outside the selector.
    pick_id: int | None = None


@dataclass(frozen=True)
class DigestDiagnostics:
    """Why a sport's send-bar result looks the way it does.

    Populated for every in-season sport the selector considers, even one
    with zero games that day or zero picks that survive -- an empty-day
    email needs to say *why* each sport is empty, and "no games" and "games
    but nothing cleared the bar" are different answers.
    """
    games: int              # games scheduled for this sport on target_date
    generated: int          # game picks after dedupe, before any filter
    survived_price: int     # of those, how many had a price inside the window
    #: blend_weight[sport], or 0.0 if missing. Recorded for reference only
    #: (see config.yaml's digest.send_bar.blend_weight comment) -- since
    #: 2026-09-30 it no longer gates or ranks anything, and is not shown in
    #: the empty-day reason text.
    lambda_used: float
    #: True only when `sport` has its own entry in blend_weight. False means
    #: lambda_used is a fallback 0.0, not a measurement.
    lambda_measured: bool = True


@dataclass(frozen=True)
class DigestSection:
    sport: str
    picks: list[DigestPick]
    props: list[DigestPick]
    record: tuple[int, int] | None = None   # (wins, losses) over the trailing window
    diagnostics: DigestDiagnostics | None = None


def _price_prob(odds: int | None) -> float | None:
    """What the quoted price implies, vig included, or None without a price.

    Raw rather than de-vigged on purpose: the email shows one side, and a
    reader can check a raw implied probability against the price by hand.
    """
    if odds is None:
        return None
    try:
        return american_to_implied_prob(odds)
    except InvalidOddsError:
        return None


TRAILING_DAYS = 30


def trailing_record(session, sport: str, target_date, days: int = TRAILING_DAYS):
    """(wins, losses) for this sport's graded game picks in the last `days`.

    Decided results only: a push is neither. Props are excluded because they
    are graded on a different scale and listed separately. None when nothing
    has been graded, which is different from 0-0 -- a sport with no history
    must not print a record.
    """
    cutoff = target_date - timedelta(days=days)
    rows = (session.query(PickResult.result)
            .join(PickModel, PickModel.id == PickResult.pick_id)
            .join(Game, Game.id == PickModel.game_id)
            .filter(Game.sport == sport,
                    Game.date >= cutoff, Game.date < target_date,
                    PickModel.pick_type != "prop",
                    PickResult.result.in_(("win", "loss")))
            .all())
    if not rows:
        return None
    wins = sum(1 for (r,) in rows if r == "win")
    return wins, len(rows) - wins


def _team_names(session, game: Game) -> tuple[str, str]:
    """(away_name, home_name), with the same fallbacks `_matchup` has used.

    One resolver, because the rendered label and the matchup line must agree:
    an email that says "Pittsburgh Pirates to win" above "Cardinals at Home"
    would be worse than the jargon it replaced.
    """
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    return (away.name if away else "Away", home.name if home else "Home")


def _matchup(session, game: Game) -> str:
    away_name, home_name = _team_names(session, game)
    return f"{away_name} at {home_name}"


def _rationale_for(session, pick: PickModel, game: Game) -> str:
    if not pick.rationale_json:
        return ""
    try:
        raw = json.loads(pick.rationale_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(raw, list):
        return ""
    factors = [
        PickFactor(code=f.get("code", ""), side=f.get("side", "home"),
                   strength=f.get("strength", "moderate"))
        for f in raw if isinstance(f, dict)
    ]
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    return render_rationale(factors,
                            home.name if home else "Home",
                            away.name if away else "Away")


def _dedupe_latest(picks: list[PickModel]) -> list[PickModel]:
    """Collapse repeated picks to the most recently created row.

    `_run_window` runs once per game window per sport per day and regenerates
    picks for ALL of today's games each time, and `generate_and_store_picks`
    inserts unconditionally (there is no unique constraint on `picks`). Three
    NBA windows therefore leave three identical "HOME ML" rows per game. Left
    alone, the digest's "top 5" is the same one or two picks repeated.

    Identity is (game_id, pick_type, pick_value). `created_at` is nullable in
    practice (rows written before the default, or by raw SQL), so a missing
    timestamp sorts oldest and never displaces a real one. `id` breaks ties,
    since it is monotonic for inserts into the same table.
    """
    latest: dict[tuple, PickModel] = {}
    for p in picks:
        key = (p.game_id, p.pick_type, p.pick_value)
        incumbent = latest.get(key)
        if incumbent is None or _recency(p) > _recency(incumbent):
            latest[key] = p
    return list(latest.values())


def _recency(pick: PickModel) -> tuple:
    created = pick.created_at
    if created is None:
        return (datetime.min, pick.id or 0)
    # Rows can come back naive or aware depending on how they were written;
    # comparing the two raises TypeError mid-sort.
    return (created.replace(tzinfo=None), pick.id or 0)


#: These four must be present and numeric. A missing or non-numeric value
#: used to default to 0.0 -- which, combined with lambda=0, used to make
#: `shrunk (0.0) >= min_shrunk_edge_pp (0.0)` true for EVERY priced pick,
#: including a negative edge. That gate is gone entirely (owner decision
#: 2026-09-30, see docs/review-remediation.md) -- picks are now ranked and
#: shown by raw edge, not admitted or rejected by it. The remaining keys
#: still fail CLOSED (raise) rather than fail open when misconfigured.
#: `blend_weight` is deliberately not in this list: a sport missing from it
#: is a documented, intentional 0.0 (see select_digest's docstring), not a
#: misconfiguration, and it no longer gates anything either way.
_REQUIRED_SEND_BAR_KEYS = (
    "min_odds", "max_odds", "max_game_picks", "max_props",
)


def _validate_send_bar(send_bar: dict | None) -> dict:
    """Return a validated copy of `send_bar`, or raise ValueError.

    Every key in `_REQUIRED_SEND_BAR_KEYS` must be present and a real number
    (not None, not a bool, not a string) -- config.yaml's `null` and a typo'd
    or removed key must both be loud failures, not a silent 0.0.

    A leftover `min_shrunk_edge_pp` key (from a config that predates the
    2026-09-30 removal of the shrunk-edge gate) is explicitly ignored, with
    a WARNING, rather than silently honoured or treated as a hard error.
    Raising here would turn an inert, no-longer-meaningful leftover key into
    an outage of the whole digest job -- worse than the thing it would be
    guarding against, since the key does nothing either way now. A WARNING
    is loud enough that it will be noticed and the stale key cleaned up.
    """
    if not isinstance(send_bar, dict):
        raise ValueError("digest.send_bar is required")
    validated = dict(send_bar)
    for key in _REQUIRED_SEND_BAR_KEYS:
        value = send_bar.get(key)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"digest.send_bar.{key} is required")
        validated[key] = value
    if "min_shrunk_edge_pp" in send_bar:
        logger.warning(
            "digest.send_bar.min_shrunk_edge_pp is set but no longer used "
            "(the shrunk-edge gate was removed 2026-09-30); ignoring it.")
    validated["blend_weight"] = send_bar.get("blend_weight") or {}
    return validated


def _start_time_key(games_by_id, p) -> tuple:
    # start_time is nullable, and stored rows may be naive or aware.
    # Comparing a datetime to a date, or a naive to an aware datetime,
    # raises TypeError mid-sort -- normalize to naive and push missing
    # start times to the end.
    st = games_by_id[p.game_id].start_time
    when = st.replace(tzinfo=None) if st is not None else datetime.max
    return (when, p.id or 0)


def _suppressed(record, min_trailing_win_pct, min_trailing_picks, sport) -> bool:
    if (min_trailing_win_pct is not None and record is not None
            and sum(record) >= min_trailing_picks
            and record[0] / sum(record) < min_trailing_win_pct):
        logger.info("Digest: %s suppressed, trailing %d-%d below %.0f%%",
                    sport, record[0], record[1], min_trailing_win_pct * 100)
        return True
    return False


def select_digest(session, target_date, sports, seasons, send_bar: dict | None = None,
                  min_trailing_win_pct: float | None = None,
                  min_trailing_picks: int = 20):
    """Return one DigestSection per in-season sport.

    A sport in season always gets a section, even one with no games that day
    or no picks priced inside the window -- ``diagnostics`` on each section
    is how the empty-day email explains itself. (The one exception is
    trailing-record suppression, below: a suppressed sport is left out
    entirely, same as before this changed.)

    Game picks must clear a "send bar" (``send_bar`` -- see config.yaml's
    digest.send_bar):

    - a stored price (``odds_at_pick``) is required; a pick with none is
      dropped -- previously it was kept and shown at -110;
    - the price must fall in [min_odds, max_odds], inclusive at both ends;
    - ranked by raw edge (``edge_pct``, the stored PickModel.edge_pct --
      model minus market, in percentage points, de-vigged for moneyline,
      measured against 0.5 for spreads/totals; see
      backend/analysis/variants/ensemble.py) descending, ties broken by
      start time then id;
    - capped at max_game_picks.

    There is no longer an edge gate (owner decision 2026-09-30, see
    docs/review-remediation.md): every priced game pick is shown, with its
    raw edge, up to the cap -- the reader judges the edge, not the selector.
    ``blend_weight`` stays in config as the recorded market-shrinkage
    measurement, but it no longer filters or ranks anything.

    Props are a separate, price-gated but not edge-gated list: only
    gradeable markets (present in MARKET_STAT_MAP) survive, the same price
    window applies, and they are sorted by start time then id (no ranking by
    edge -- a prop's edge_pct is price-blind and not comparable to a game
    pick's de-vigged edge; it is never shown for a prop, see render.py),
    capped at max_props.
    """
    bar = _validate_send_bar(send_bar)
    blend_weight = bar["blend_weight"]
    min_odds = bar["min_odds"]
    max_odds = bar["max_odds"]
    max_game_picks = bar["max_game_picks"]
    max_props = bar["max_props"]

    sections: list[DigestSection] = []

    for sport in sports:
        if not is_sport_in_season(sport, seasons, target_date):
            continue

        lambda_measured = sport in blend_weight
        lam = float(blend_weight.get(sport, 0.0))

        games = (
            session.query(Game)
            .filter(Game.sport == sport, Game.date == target_date)
            .all()
        )
        games_by_id = {g.id: g for g in games}

        if not games:
            record = trailing_record(session, sport, target_date)
            if _suppressed(record, min_trailing_win_pct, min_trailing_picks, sport):
                continue
            sections.append(DigestSection(
                sport=sport, picks=[], props=[], record=record,
                diagnostics=DigestDiagnostics(games=0, generated=0,
                                              survived_price=0, lambda_used=lam,
                                              lambda_measured=lambda_measured),
            ))
            continue

        # `prop_pipeline` writes its analyzed props into this SAME picks table
        # with pick_type="prop". They must never be ranked against game picks:
        # a prop's edge_pct is (prob - 0.5) * 200, which ignores the prop's
        # price, while a game pick's edge is measured against the de-vigged
        # market. Mixing them floats juiced props above better game picks.
        picks = (
            session.query(PickModel)
            .filter(PickModel.game_id.in_(list(games_by_id)),
                    PickModel.confidence >= 1,
                    PickModel.pick_type != "prop")
            .all()
        )
        picks = _dedupe_latest(picks)
        generated = len(picks)

        priced = [p for p in picks if p.odds_at_pick is not None
                 and min_odds <= p.odds_at_pick <= max_odds]
        survived_price = len(priced)

        # No edge gate: every priced game pick qualifies. Ranked by raw
        # edge_pct descending, ties broken by start time then id.
        priced.sort(key=lambda p: (-(p.edge_pct or 0.0), *_start_time_key(games_by_id, p)))

        def _make_pick(p: PickModel) -> DigestPick:
            game = games_by_id[p.game_id]
            away_name, home_name = _team_names(session, game)
            return DigestPick(
                sport=sport,
                matchup=_matchup(session, game),
                pick_value=p.pick_value,
                odds=p.odds_at_pick or -110,
                confidence=p.confidence,
                edge_pct=round(p.edge_pct or 0.0, 1),
                rationale=_rationale_for(session, p, game),
                model_prob=p.model_prob,
                price_prob=_price_prob(p.odds_at_pick),
                home_team=home_name,
                away_team=away_name,
                pick_id=p.id,
            )

        digest_picks = [_make_pick(p) for p in priced[:max_game_picks]]

        # Props: gradeable markets only, same price window, no edge gate.
        props = (
            session.query(PickModel)
            .filter(PickModel.game_id.in_(list(games_by_id)),
                    PickModel.confidence >= 1,
                    PickModel.pick_type == "prop")
            .all()
        )
        props = _dedupe_latest(props)
        gradeable_props = [
            p for p in props
            if p.prop_market in MARKET_STAT_MAP
            and p.odds_at_pick is not None
            and min_odds <= p.odds_at_pick <= max_odds
        ]
        gradeable_props.sort(key=lambda p: _start_time_key(games_by_id, p))

        def _make_prop(p: PickModel) -> DigestPick:
            game = games_by_id[p.game_id]
            away_name, home_name = _team_names(session, game)
            return DigestPick(
                sport=sport,
                matchup=_matchup(session, game),
                pick_value=p.pick_value,
                odds=p.odds_at_pick or -110,
                confidence=p.confidence,
                edge_pct=round(p.edge_pct or 0.0, 1),
                rationale=_rationale_for(session, p, game),
                home_team=home_name,
                away_team=away_name,
                pick_id=p.id,
            )

        digest_props = [_make_prop(p) for p in gradeable_props[:max_props]]

        record = trailing_record(session, sport, target_date)
        if _suppressed(record, min_trailing_win_pct, min_trailing_picks, sport):
            continue

        sections.append(DigestSection(
            sport=sport, picks=digest_picks, props=digest_props, record=record,
            diagnostics=DigestDiagnostics(games=len(games), generated=generated,
                                          survived_price=survived_price, lambda_used=lam,
                                          lambda_measured=lambda_measured),
        ))

    return sections
