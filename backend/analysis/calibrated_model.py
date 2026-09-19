from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from sklearn.linear_model import LogisticRegression
from sqlalchemy import and_

from backend.data_types import GameData
from backend.models import Game, TeamStat, EloRating

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MIN_TRAINING_GAMES = 30

#: Fixed sport ordering for the one-hot slots. Deliberately a constant and
#: not derived from the training data: a vocabulary built from whatever
#: sports happen to be present would reassign column meanings between runs,
#: so a model fitted one night would be read wrong the next.
#:
#: A sport absent from this tuple sets no slot and falls back to the shared
#: intercept -- the old behaviour, which is a safe degradation.
SPORT_VOCAB: tuple[str, ...] = (
    "boxing", "mlb", "mma", "nba", "ncaab", "ncaaf", "nfl",
)


def build_feature_row(
    elo_diff: float,
    point_diff: float,
    net_rating_diff: float,
    rest_days_diff: float,
    pace_diff: float,
    sport: str,
    neutral_site: bool = False,
) -> list[float]:
    """The feature vector for CalibratedModel, for training AND prediction.

    Five legacy difference features, then one one-hot slot per sport.

    The sport slots exist because home advantage has no other representation
    in this model: there is no home feature, so it is carried entirely by the
    intercept. One intercept across a pool that is 91% nba scored ncaab -- a
    0.708 home-win sport -- as though it were 0.561.

    Both call sites route through here. They previously duplicated the
    five-element list, which is how the two could have drifted.

    A slot means "home advantage for this sport is in effect", so a neutral
    venue fires none of them: there is no host to hold an advantage, and the
    bare intercept then carries what a no-host game looks like. This matters
    concretely -- every ncaab game in the database is an NCAA tournament game
    at a neutral site, where "home team" is a bracket seed designation. Left
    ungated the model reads their 0.708 home rate as home-court advantage
    when it is seed strength, and would carry that into November when real
    home-court games arrive.

    Gating alone is not sufficient, and the trailing slot is why. With the
    slots gated, a neutral game encodes as all-zeros -- and so does a sport
    absent from the vocabulary. The model cannot tell "no host" from "sport I
    have never seen", so neutral outcomes end up fitting the bare intercept
    and every slot-less sport inherits them. Measured on a fixture with
    hosted nba at 0.55 and all-neutral ncaab at 0.75, a *hosted* ncaab game
    came back 0.740: the seed effect was not removed, it was promoted from
    ncaab's slot into the global intercept.

    The explicit neutral slot gives those rows somewhere of their own to go,
    leaving the intercept to mean "hosted game, sport unknown".
    """
    row = [elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff]
    home = 0.0 if neutral_site else 1.0
    row.extend(home if sport == s else 0.0 for s in SPORT_VOCAB)
    row.append(1.0 - home)
    return row


# Canonical feature order — FEATURE_ORDER is the single source of truth.
# extract_features() must return a dict with exactly these keys.
FEATURE_ORDER = [
    "elo_diff", "point_diff", "net_rating_diff",
    "home_rest_days", "away_rest_days", "pace_diff",
    "home_flag",
    "offensive_rating_home", "offensive_rating_away",
    "defensive_rating_home", "defensive_rating_away",
    "back_to_back_home", "back_to_back_away",
]


def extract_features(game: GameData) -> dict:
    """Extract expanded feature set from a GameData instance.

    Returns a dict of feature_name -> value for use with LightGBM or logistic regression.
    """
    hs, aws = game.home_stats, game.away_stats
    return {
        "elo_diff": hs.elo_rating - aws.elo_rating,
        "point_diff": hs.point_diff - aws.point_diff,
        "net_rating_diff": (hs.offensive_rating - hs.defensive_rating)
                          - (aws.offensive_rating - aws.defensive_rating),
        "home_rest_days": float(hs.rest_days),
        "away_rest_days": float(aws.rest_days),
        "pace_diff": hs.pace - aws.pace,
        "home_flag": 1,
        "offensive_rating_home": hs.offensive_rating,
        "offensive_rating_away": aws.offensive_rating,
        "defensive_rating_home": hs.defensive_rating,
        "defensive_rating_away": aws.defensive_rating,
        "back_to_back_home": 1 if hs.rest_days <= 1 else 0,
        "back_to_back_away": 1 if aws.rest_days <= 1 else 0,
    }


def features_to_array(features: dict) -> list[float]:
    """Convert feature dict to ordered array for model input.

    FEATURE_ORDER is the single source of truth for feature ordering.
    """
    return [features.get(k, 0.0) for k in FEATURE_ORDER]


def _fallback_probability(game: GameData) -> float:
    """Heuristic sigmoid probability used when the model is not trained."""
    hs, aws = game.home_stats, game.away_stats
    pd_diff = hs.point_diff - aws.point_diff
    pd_score = 1 / (1 + 10 ** (-pd_diff / 10))
    elo_diff = hs.elo_rating - aws.elo_rating
    elo_score = 1 / (1 + 10 ** (-elo_diff / 400))
    net_home = hs.offensive_rating - hs.defensive_rating
    net_away = aws.offensive_rating - aws.defensive_rating
    net_diff = net_home - net_away
    rating_score = 1 / (1 + 10 ** (-net_diff / 10))
    from backend.analysis.sport_constants import get_home_win_rate
    hca = get_home_win_rate(game.sport)
    prob = 0.3 * pd_score + 0.35 * elo_score + 0.25 * rating_score + 0.1 * hca
    return max(0.01, min(0.99, prob))


def _stat_value(stats: list[TeamStat], stat_type: str) -> float | None:
    """Look up a stat value by type from a list of TeamStat rows."""
    for s in stats:
        if s.stat_type == stat_type:
            return s.value
    return None


class CalibratedModel:
    """Logistic regression model trained on historical game outcomes."""

    def __init__(self) -> None:
        self.model: LogisticRegression | None = None
        self.trained = False
        self.n_training_games = 0

    def train_from_db(self, session: Session) -> None:
        """Query completed games and train the logistic regression model.

        The model is trained on features derived from TeamStat and EloRating
        rows stored at the time each game was played.  The target is whether
        the home team won (1) or not (0).
        """
        completed_games = (
            session.query(Game)
            .filter(
                and_(
                    Game.status == "final",
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .all()
        )

        if len(completed_games) < MIN_TRAINING_GAMES:
            logger.info(
                "Only %d final games found (need %d). Skipping calibration.",
                len(completed_games),
                MIN_TRAINING_GAMES,
            )
            self.trained = False
            return

        X: list[list[float]] = []
        y: list[int] = []

        # Point-in-time ELO from history table
        from backend.models import EloHistory
        elo_history_map: dict[tuple[int, int], float] = {}
        for eh in session.query(EloHistory).all():
            elo_history_map[(eh.team_id, eh.game_id)] = eh.rating

        # Fallback: current ELO for games without history
        elo_current: dict[int, float] = {}
        for elo in session.query(EloRating).all():
            elo_current[elo.team_id] = elo.rating

        for game in completed_games:
            home_stats = (
                session.query(TeamStat)
                .filter(
                    and_(
                        TeamStat.game_id == game.id,
                        TeamStat.team_id == game.home_team_id,
                    )
                )
                .all()
            )
            away_stats = (
                session.query(TeamStat)
                .filter(
                    and_(
                        TeamStat.game_id == game.id,
                        TeamStat.team_id == game.away_team_id,
                    )
                )
                .all()
            )

            # Extract individual stat values, defaulting to 0 if missing
            home_elo = elo_history_map.get(
                (game.home_team_id, game.id),
                elo_current.get(game.home_team_id, 1500.0)
            )
            away_elo = elo_history_map.get(
                (game.away_team_id, game.id),
                elo_current.get(game.away_team_id, 1500.0)
            )
            elo_diff = home_elo - away_elo

            home_pd = _stat_value(home_stats, "point_diff") or 0.0
            away_pd = _stat_value(away_stats, "point_diff") or 0.0
            point_diff = home_pd - away_pd

            home_off = _stat_value(home_stats, "offensive_rating") or 100.0
            home_def = _stat_value(home_stats, "defensive_rating") or 100.0
            away_off = _stat_value(away_stats, "offensive_rating") or 100.0
            away_def = _stat_value(away_stats, "defensive_rating") or 100.0
            net_rating_diff = (home_off - home_def) - (away_off - away_def)

            home_rest = _stat_value(home_stats, "rest_days") or 1.0
            away_rest = _stat_value(away_stats, "rest_days") or 1.0
            rest_days_diff = home_rest - away_rest

            home_pace = _stat_value(home_stats, "pace") or 100.0
            away_pace = _stat_value(away_stats, "pace") or 100.0
            pace_diff = home_pace - away_pace

            features = build_feature_row(
                elo_diff, point_diff, net_rating_diff, rest_days_diff,
                pace_diff, game.sport, bool(game.neutral_site),
            )
            label = 1 if game.home_score > game.away_score else 0

            X.append(features)
            y.append(label)

        X_arr = np.array(X, dtype=np.float64)
        y_arr = np.array(y, dtype=np.int64)

        self.model = LogisticRegression(max_iter=1000)
        self.model.fit(X_arr, y_arr)
        self.trained = True
        self.n_training_games = len(y)
        logger.info(
            "Calibrated model trained on %d games. Coefficients: %s",
            self.n_training_games,
            self.model.coef_.tolist(),
        )

    def predict_home_win_prob(self, game: GameData) -> float:
        """Return calibrated P(home win).

        Falls back to the heuristic sigmoid when the model has not been
        trained (fewer than MIN_TRAINING_GAMES completed games available).
        """
        if not self.trained or self.model is None:
            return _fallback_probability(game)

        feature_dict = extract_features(game)
        feature_arr = np.array([build_feature_row(
            feature_dict["elo_diff"],
            feature_dict["point_diff"],
            feature_dict["net_rating_diff"],
            feature_dict["home_rest_days"] - feature_dict["away_rest_days"],
            feature_dict["pace_diff"],
            game.sport,
            bool(game.neutral_site),
        )], dtype=np.float64)
        proba = self.model.predict_proba(feature_arr)
        # predict_proba returns [[P(class0), P(class1)]]
        # class 1 = home win
        return float(proba[0][1])
