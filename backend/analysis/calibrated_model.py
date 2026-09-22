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
#: Season phases whose results do not describe competitive basketball and
#: must not train a model that prices real games.
NON_COMPETITIVE_PHASES: tuple[str, ...] = ("allstar", "preseason")

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


def shrink_sport_coefficients(coefs: dict[str, float],
                              counts: dict[str, int]) -> dict[str, float]:
    """Partial-pool per-sport home-advantage coefficients toward their mean.

    Each sport gets its own one-hot slot in :func:`build_feature_row`,
    because home advantage has no other representation in this model. Every
    slot is then fitted freely, so a coefficient supported by 31 games
    carries the same authority as one supported by 1,253 -- which is how
    nfl came to produce a 49% moneyline edge from a 31-game intercept.

        w_s      = n_s / (n_s + K)
        shrunk_s = mu + (coef_s - mu) * w_s

    ``mu`` is the games-weighted mean of the sport coefficients. ``K`` is
    derived rather than chosen: for a binary outcome the sampling variance
    of a slot's coefficient goes as ``1 / (n * p(1-p))``, which is ``4/n``
    at p = 0.5, so the empirical-Bayes weight ``tau^2 / (tau^2 + 4/n)``
    gives ``K = 4 / tau^2`` with ``tau^2`` the between-sport variance
    actually observed. A hand-tuned constant here would be a free parameter
    fitted to nothing.

    This is what a random intercept computes. `BinomialBayesMixedGLM` would
    fit it as one, and is the textbook answer, but statsmodels is not
    installed and swapping a deterministic `LogisticRegression` for a
    variational-Bayes fit that can fail to converge is not a trade worth
    making for an unproven gain.

    Two degenerate cases return the input unchanged, because there is
    genuinely nothing to pool: a single sport (no pool to shrink toward)
    and zero between-sport variance (the sports already agree).
    """
    # No explicit single-sport guard: one sport has zero between-sport
    # variance by construction, which the `k is None` branch below already
    # returns unchanged. A second check would be an untested branch that can
    # never change an answer.
    #
    # Only sports with training games can inform the pool. A sport with no
    # games contributes no evidence to `mu` or `tau2` -- including it would
    # let a slot fitted from nothing drag the mean it is about to be
    # shrunk toward.
    informative = {s: n for s in coefs if (n := max(0, counts.get(s, 0))) > 0}
    if not informative:
        return dict(coefs)

    total = sum(informative.values())
    mu = sum(coefs[s] * n for s, n in informative.items()) / total
    tau2 = sum(n * (coefs[s] - mu) ** 2 for s, n in informative.items()) / total

    # 4 = 1 / (p * (1 - p)) at p = 0.5, the variance factor of a binary
    # outcome. Not a tuning knob.
    k = (4.0 / tau2) if tau2 > 0 else None

    shrunk = {}
    for sport, coef in coefs.items():
        n = informative.get(sport, 0)
        if n == 0:
            # No evidence of its own, so the pooled mean IS the estimate.
            # Better than the 0.0 an unfitted slot carries, which reads as
            # "this sport has no home advantage" rather than "unknown".
            shrunk[sport] = mu
        elif k is None:
            # The sports that DO have data agree, so there is no
            # between-sport variance to pool away from. Their own estimates
            # stand; shrinking them to their own mean would be a no-op
            # dressed up as a correction.
            shrunk[sport] = coef
        else:
            shrunk[sport] = mu + (coef - mu) * (n / (n + k))
    return shrunk


#: Index of the first per-sport slot in a `build_feature_row` vector.
#:
#: DERIVED from the function itself rather than written as 5, so a new
#: leading feature moves this automatically instead of silently shifting
#: which coefficient the pooling reads. The trailing -1 is the neutral-site
#: slot, which sits after the sport slots and is not one of them.
SPORT_SLOT_OFFSET = (len(build_feature_row(0, 0, 0, 0, 0, SPORT_VOCAB[0]))
                     - len(SPORT_VOCAB) - 1)


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
        #: Set by `train_from_db`: the per-sport home-advantage slots before
        #: and after partial pooling, with the hosted-game counts behind them.
        self.sport_pooling: dict | None = None

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
                    # Exhibitions are not results. The 2026 NBA All-Star
                    # round robin sits in `games` as three nba finals with
                    # totals of 72, 82 and 93 against a real average of
                    # 230.9, and its squads are not teams anyone bets on.
                    # `unknown` is kept: it is what every row predating
                    # season_type carries, and dropping those would throw
                    # away most of the history.
                    Game.season_type.notin_(NON_COMPETITIVE_PHASES),
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

        sport_counts: dict[str, int] = {}
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
            # Only a HOSTED game informs a sport's home-advantage slot: a
            # neutral game fires no slot (see build_feature_row), so counting
            # it would overstate the evidence behind that coefficient.
            if not game.neutral_site:
                sport_counts[game.sport] = sport_counts.get(game.sport, 0) + 1

        X_arr = np.array(X, dtype=np.float64)
        y_arr = np.array(y, dtype=np.int64)

        self.model = LogisticRegression(max_iter=1000)
        self.model.fit(X_arr, y_arr)

        # Partial-pool the per-sport home-advantage slots. Fitted freely,
        # nfl's coefficient (31 hosted games) carried the same authority as
        # nba's (1,253) -- which is how a 31-game intercept produced a 49%
        # moneyline edge. Logged before and after, unconditionally: a
        # silent adjustment to what the model believes is exactly the kind
        # of change nobody would find later.
        offset = SPORT_SLOT_OFFSET
        coefs = self.model.coef_[0]
        before = {sport: float(coefs[offset + i])
                  for i, sport in enumerate(SPORT_VOCAB)}
        after = shrink_sport_coefficients(before, sport_counts)
        for i, sport in enumerate(SPORT_VOCAB):
            coefs[offset + i] = after[sport]
        #: What pooling did, kept for inspection. The fitted coefficients
        #: alone cannot show it -- the raw values are overwritten -- so
        #: without this there is no way to check the adjustment landed on
        #: the sport slots rather than on the five leading features.
        self.sport_pooling = {"hosted_games": dict(sport_counts),
                              "before": before, "after": after}
        logger.info(
            "Sport home-advantage slots partial-pooled (hosted games %s):"
            " %s -> %s",
            {k: sport_counts.get(k, 0) for k in SPORT_VOCAB},
            {k: round(v, 3) for k, v in before.items()},
            {k: round(v, 3) for k, v in after.items()},
        )

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
