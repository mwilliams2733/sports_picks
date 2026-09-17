import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.orm import Session
from backend.models import Game, PickModel, StrategyModel, Odds, TeamStat, EloRating, EloHistory
from backend.data_types import GameData, TeamStats, OddsSnapshot, FighterStats
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.variants.recent_form import RecentFormStrategy
from backend.analysis.variants.value_only import ValueOnlyStrategy
from backend.analysis.variants.sport_specific import SportSpecificStrategy
from backend.analysis.variants.prop_value import PropValueStrategy
from backend.analysis.variants.combat_sports import CombatSportsStrategy
from backend.analysis.confidence import get_thresholds

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "ensemble": EnsembleStrategy,
    "recent_form": RecentFormStrategy,
    "value_only": ValueOnlyStrategy,
    "sport_specific": SportSpecificStrategy,
    "prop_value": PropValueStrategy,
    "combat_sports": CombatSportsStrategy,
}

def generate_and_store_picks(session: Session, strategy_id: int,
                              target_date: date | None = None,
                              pitcher_scores: dict[int, dict[str, float]] | None = None) -> int:
    target_date = target_date or date.today()
    strat_row = session.get(StrategyModel, strategy_id)
    if not strat_row: return 0
    config = json.loads(strat_row.config_json)
    strategy_cls = STRATEGY_MAP.get(strat_row.name)
    if not strategy_cls: return 0
    games = session.query(Game).filter(Game.date == target_date, Game.status == "scheduled").all()
    count = 0
    thresholds_by_sport: dict[str, dict] = {}
    for game in games:
        try:
            if game.sport not in thresholds_by_sport:
                thresholds_by_sport[game.sport] = get_thresholds(session, game.sport)
            sport_thresholds = thresholds_by_sport[game.sport]
            # Combat sports always route to CombatSportsStrategy because the team-based
            # strategies have no signal for individual fighters. For team sports, use
            # whichever strategy the user configured.
            if game.sport in ("mma", "boxing"):
                strategy = CombatSportsStrategy(strat_row.name, config, sport_thresholds)
            else:
                strategy = strategy_cls(strat_row.name, config, sport_thresholds)
            game_data = _build_game_data(session, game, pitcher_scores=pitcher_scores)
            if game.sport in ("mma", "boxing"):
                game_data.home_fighter = _build_fighter_stats(session, game.home_team_id, game.sport, game.date)
                game_data.away_fighter = _build_fighter_stats(session, game.away_team_id, game.sport, game.date)
            picks = strategy.predict(game_data)
            for pick in picks:
                if pick.confidence >= 1:
                    db_pick = PickModel(game_id=game.id, strategy_id=strategy_id,
                        pick_type=pick.pick_type, pick_value=pick.pick_value,
                        confidence=pick.confidence, edge_pct=pick.edge_pct,
                        odds_at_pick=pick.odds_at_pick,
                        model_prob=pick.model_probability,
                        rationale_json=json.dumps([asdict(f) for f in pick.factors]),
                        created_at=datetime.now(tz=timezone.utc))
                    session.add(db_pick)
                    count += 1
        except Exception:
            logger.exception("Pick generation failed for game %s", game.id)
            continue
    session.commit()
    return count

def _check_schedule_fatigue(session: Session, team_id: int, game_date: date, sport: str) -> tuple[bool, float]:
    """Check if team is playing 3rd game in 4 nights."""
    if sport not in ("nba", "ncaab"):
        return False, 0.0

    four_days_ago = game_date - timedelta(days=3)
    recent_games = session.query(Game).filter(
        Game.date >= four_days_ago,
        Game.date < game_date,
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
    ).count()

    if recent_games >= 2:  # 2 games in last 3 days + today = 3 in 4
        severity = min(recent_games / 3, 1.0)  # Scale severity
        return True, severity
    return False, 0.0


def _check_lookahead_spot(session: Session, team_id: int, opponent_team_id: int,
                          game_date: date, sport: str,
                          team_elo: float, opponent_elo: float) -> bool:
    """Check if this is a lookahead spot - easy game before a tough one."""
    # Only relevant for weekly sports (NFL, NCAAF) or when games are spaced out
    if sport in ("nfl", "ncaaf"):
        look_ahead_window = timedelta(days=10)
    else:
        look_ahead_window = timedelta(days=4)

    # Current game is a mismatch (team is heavily favored)
    elo_diff = team_elo - opponent_elo
    if elo_diff < 100:  # Not a big favorite, no lookahead risk
        return False

    # Check if next game is against a strong opponent
    next_game = session.query(Game).filter(
        Game.date > game_date,
        Game.date <= game_date + look_ahead_window,
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
    ).order_by(Game.date).first()

    if not next_game:
        return False

    # Get next opponent's ELO -- on the SAME basis as `team_elo`, which comes
    # from `_team_elo` and is therefore the replayed `elo_history` rating. The
    # raw `EloRating` query this replaces put the two sides of the comparison
    # below on tables that disagree by a mean of 53 points and up to 134, wider
    # than the 50-point band itself.
    #
    # `game_date`, not `next_game.date`, and no game id. `next_game` is selected
    # with `Game.date > game_date`, so bounding by its date would admit ratings
    # from games between the predicted game and the next one; passing
    # `next_game.id` would hit the exact-match step and return `next_game`'s own
    # pre-game rating. Both are after `game_date` and neither exists at
    # prediction time. This asks "how strong is that opponent as far as anyone
    # knows today", which is the question the heuristic means.
    next_opp_id = next_game.away_team_id if next_game.home_team_id == team_id else next_game.home_team_id
    next_opp_elo = _team_elo_or_none(session, next_opp_id, sport, None, game_date)

    # Deliberately the None-aware resolver: an opponent with no rating anywhere
    # is unknown, not average. Letting `_team_elo`'s 1500.0 default carry this
    # would answer True for any `team_elo` below 1550 on no evidence at all.
    # The original row-or-None query fell through to `return False` here, and
    # that behaviour is preserved.
    if next_opp_elo is not None and next_opp_elo > team_elo - 50:
        # Next opponent is roughly equal or better
        return True

    return False


def _build_game_data(session: Session, game,
                     pitcher_scores: dict[int, dict[str, float]] | None = None) -> GameData:
    home_stats = _get_team_stats(session, game.home_team_id, game.sport,
                                 game_id=game.id, game_date=game.date)
    away_stats = _get_team_stats(session, game.away_team_id, game.sport,
                                 game_id=game.id, game_date=game.date)
    odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    odds = [OddsSnapshot(bookmaker=o.bookmaker, moneyline_home=o.moneyline_home or 0,
        moneyline_away=o.moneyline_away or 0, spread_home=o.spread_home or 0.0,
        spread_away=o.spread_away or 0.0, over_under=o.over_under or 0.0) for o in odds_rows]

    # Schedule context
    h_fatigued, h_fatigue_score = _check_schedule_fatigue(session, game.home_team_id, game.date, game.sport)
    a_fatigued, a_fatigue_score = _check_schedule_fatigue(session, game.away_team_id, game.date, game.sport)
    home_stats.is_schedule_fatigued = h_fatigued
    home_stats.schedule_fatigue_score = h_fatigue_score
    away_stats.is_schedule_fatigued = a_fatigued
    away_stats.schedule_fatigue_score = a_fatigue_score

    h_lookahead = _check_lookahead_spot(session, game.home_team_id, game.away_team_id,
                                         game.date, game.sport,
                                         home_stats.elo_rating, away_stats.elo_rating)
    a_lookahead = _check_lookahead_spot(session, game.away_team_id, game.home_team_id,
                                         game.date, game.sport,
                                         away_stats.elo_rating, home_stats.elo_rating)
    home_stats.is_lookahead_spot = h_lookahead
    away_stats.is_lookahead_spot = a_lookahead

    # MLB: attach probable-pitcher skill score if the caller has pre-computed it.
    if game.sport == "mlb" and pitcher_scores and game.id in pitcher_scores:
        ps = pitcher_scores[game.id]
        home_stats.pitcher_skill_score = ps.get("home")
        away_stats.pitcher_skill_score = ps.get("away")

    return GameData(game_id=game.id, sport=game.sport, date=game.date,
        home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        home_stats=home_stats, away_stats=away_stats, odds=odds, week=game.week)

def _build_fighter_stats(session: Session, fighter_id: int, sport: str, before_date) -> FighterStats:
    """Build FighterStats from EloRating + last-5-fights history before `before_date`."""
    elo_row = (session.query(EloRating)
               .filter(EloRating.team_id == fighter_id, EloRating.sport == sport)
               .first())
    elo_rating = elo_row.rating if elo_row else 1500.0

    past_fights = (session.query(Game)
                   .filter(Game.sport == sport, Game.status == "final",
                           Game.date < before_date,
                           ((Game.home_team_id == fighter_id) | (Game.away_team_id == fighter_id)))
                   .order_by(Game.date.desc())
                   .limit(5).all())
    if not past_fights:
        return FighterStats(elo_rating=elo_rating, recent_form_score=0.5,
                            opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    wins = 0
    opponent_elos: list[float] = []
    for f in past_fights:
        if f.home_team_id == fighter_id:
            won = (f.home_score or 0) > (f.away_score or 0)
            opp_id = f.away_team_id
        else:
            won = (f.away_score or 0) > (f.home_score or 0)
            opp_id = f.home_team_id
        if won:
            wins += 1
        opp_elo = (session.query(EloRating)
                   .filter(EloRating.team_id == opp_id, EloRating.sport == sport)
                   .first())
        if opp_elo:
            opponent_elos.append(opp_elo.rating)
    days_since = (before_date - past_fights[0].date).days
    return FighterStats(
        elo_rating=elo_rating,
        recent_form_score=wins / len(past_fights),
        opponent_avg_elo=sum(opponent_elos) / len(opponent_elos) if opponent_elos else None,
        fights_count=len(past_fights),
        days_since_last_fight=days_since,
    )


def _team_stat_rows(session: Session, team_id: int,
                    game_id: int | None, game_date: date | None) -> list[TeamStat]:
    """The TeamStat rows that apply to `team_id` going into a specific game.

    Previously this filtered on `team_id` alone, so whichever games happened to
    have rows supplied stats for *every* game that team ever played. In
    production exactly one game had rows, which is why some picks carried
    recent_form / net_rating rationale factors and others did not.

    Rows are written point-in-time (computed strictly before their own game by
    `backend.pipeline.team_stats`), so this game's own rows are the correct,
    non-leaking answer. A scheduled game usually has none yet, so the fallback
    is the team's most recent *strictly earlier* game that does. That is stale
    by at most one game and can never reach forward in time. With no game
    context at all, or no prior rows, the caller's defaults apply.
    """
    if game_id is None:
        return []

    rows = (session.query(TeamStat)
            .filter(TeamStat.team_id == team_id, TeamStat.game_id == game_id)
            .all())
    if rows or game_date is None:
        return rows

    # The team must actually have played the fallback game. Production holds
    # legacy rows attaching 33 different teams' stats to one game (1014); without
    # this guard the fallback could hand a team a stat line from a game it was
    # never in.
    latest_prior = (session.query(Game.id)
                    .join(TeamStat, TeamStat.game_id == Game.id)
                    .filter(TeamStat.team_id == team_id,
                            # `<` rather than `<=` only as a tie-break: rows are
                            # already written point-in-time, so a same-day game's
                            # row cannot contain that day's results and `<=`
                            # would be benign here -- it would merely pick a
                            # fresher row. This is untested for that reason: the
                            # consequence of getting it wrong is one extra game
                            # of staleness, not leakage. Contrast
                            # `team_stats.strictly_before`, where `<=` DOES leak
                            # and is covered by a mutation test.
                            Game.date < game_date,
                            ((Game.home_team_id == team_id)
                             | (Game.away_team_id == team_id)))
                    .order_by(Game.date.desc(), Game.id.desc())
                    .first())
    if latest_prior is None:
        return []
    return (session.query(TeamStat)
            .filter(TeamStat.team_id == team_id,
                    TeamStat.game_id == latest_prior[0])
            .all())


def _team_elo(session: Session, team_id: int, sport: str,
              game_id: int | None, game_date: date | None = None) -> float:
    """The Elo rating this team carried INTO `game_id`.

    `EloHistory` rows are written pre-game by `backend.pipeline.team_stats`,
    and `calibrated_model` trains on exactly this lookup. Reading the current
    `EloRating` instead -- an end-of-history rating that already reflects the
    outcome being predicted -- was both lookahead during historical replay and
    a train/serve skew once the history table was populated.

    Three sources, in order:

    1. the game's own `EloHistory` row, written pre-game;
    2. failing that, the team's most recent *strictly earlier* `EloHistory`
       row in this sport -- the replayed rating it carried out of its last
       played game;
    3. failing that, the current `EloRating`, then 1500.0.

    Step 2 is not an optimisation, it is the whole point of this function for
    live picks. A genuinely upcoming game has no `EloHistory` row of its own,
    so without it every live pick fell through to `EloRating` -- and for team
    sports that table is *not* the pre-game rating. It is written in exactly
    one place, `backtesting.historical.compute_historical_elo`, whose only
    entry point `load_historical_data` has no callers in this repo: not the
    scheduler, not an API route. It is therefore frozen at whatever a past
    manual run left, on a different replay basis from the `elo_history` the
    model now trains on. Measured on the backfilled production copy the two
    disagree by 53 points on average and up to 134, in both directions, so no
    intercept absorbs it. Step 2 puts serving back on the training basis.

    Deliberately NOT fixed by writing replayed ratings back into `EloRating`:
    that would change the live serving table as a side effect of a training-data
    change (see `team_stats.py:327-329`).
    """
    rating = _team_elo_or_none(session, team_id, sport, game_id, game_date)
    return rating if rating is not None else 1500.0


def _team_elo_or_none(session: Session, team_id: int, sport: str,
                      game_id: int | None,
                      game_date: date | None = None) -> float | None:
    """`_team_elo` without the 1500.0 default: None means "no rating anywhere".

    Split out so that a caller which must distinguish "unknown" from "average"
    -- `_check_lookahead_spot` does -- shares this resolution order instead of
    reimplementing it against one of the tables.
    """
    if game_id is not None:
        hist = (session.query(EloHistory)
                .filter(EloHistory.team_id == team_id,
                        EloHistory.game_id == game_id)
                .first())
        if hist is not None:
            return hist.rating

    if game_date is not None:
        prior = (session.query(EloHistory)
                 .join(Game, Game.id == EloHistory.game_id)
                 .filter(EloHistory.team_id == team_id,
                         EloHistory.sport == sport,
                         # Strictly earlier. Step 1 already answered for the
                         # game's own row, so `<=` could only reach a *different*
                         # same-day game's pre-game rating -- staleness in
                         # reverse, not leakage, which is why this carries a
                         # comment rather than a mutation test. Contrast
                         # `team_stats.strictly_before`, where `<=` does leak.
                         Game.date < game_date)
                 .order_by(Game.date.desc(), Game.id.desc())
                 .first())
        if prior is not None:
            return prior.rating

    row = (session.query(EloRating)
           .filter(EloRating.team_id == team_id, EloRating.sport == sport)
           .first())
    return row.rating if row else None


def _get_team_stats(session: Session, team_id: int, sport: str,
                    game_id: int | None = None,
                    game_date: date | None = None) -> TeamStats:
    stats_rows = _team_stat_rows(session, team_id, game_id, game_date)
    stats_dict = {s.stat_type: s.value for s in stats_rows}
    elo_rating = _team_elo(session, team_id, sport, game_id, game_date)
    return TeamStats(point_diff=stats_dict.get("point_diff", 0.0),
        home_record=(int(stats_dict.get("home_wins", 0)), int(stats_dict.get("home_losses", 0))),
        away_record=(int(stats_dict.get("away_wins", 0)), int(stats_dict.get("away_losses", 0))),
        last_n_record=(int(stats_dict.get("last_n_wins", 0)), int(stats_dict.get("last_n_losses", 0))),
        offensive_rating=stats_dict.get("offensive_rating", 100.0),
        defensive_rating=stats_dict.get("defensive_rating", 100.0),
        pace=stats_dict.get("pace", 100.0), strength_of_schedule=stats_dict.get("sos", 0.5),
        elo_rating=elo_rating, rest_days=int(stats_dict.get("rest_days", 2)),
        turnover_margin=stats_dict.get("turnover_margin"), red_zone_pct=stats_dict.get("red_zone_pct"),
        conference_strength=stats_dict.get("conference_strength"))
