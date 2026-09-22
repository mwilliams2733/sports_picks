from backend.models import PlayerProp, PlayerStat
from backend.data_types import PropAnalysis
from backend.analysis.prop_confidence import calculate_prop_confidence
from backend.analysis.prop_markets import MARKET_STAT_MAP

try:
    from scipy.stats import norm, poisson

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# Count-based markets where Poisson is appropriate for low means
_COUNT_BASED_MARKETS = {
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_steals",
    "player_blocks",
    "player_turnovers",
}

# Minimum data points needed for variance-based analysis
_MIN_VARIANCE_SAMPLES = 3


def _compute_exceedance_prob(
    mean: float, variance: float, line: float, market: str
) -> float:
    """Compute P(X > line) using an appropriate distribution.

    For count-based markets with low mean (< 10), uses Poisson.
    Otherwise uses Normal distribution.
    Returns a probability between 0 and 1.
    """
    if not _HAS_SCIPY:
        # Fallback: simple comparison
        return 1.0 if mean > line else 0.0

    std = variance**0.5

    # Use Poisson for count-based stats with low mean
    if market in _COUNT_BASED_MARKETS and mean < 10 and mean > 0:
        # Poisson parameter is the mean
        # P(X > line) = 1 - P(X <= floor(line))
        # For a line of e.g. 5.5, P(X > 5.5) = P(X >= 6) = 1 - P(X <= 5)
        import math

        return float(1.0 - poisson.cdf(math.floor(line), mu=mean))

    # Normal distribution
    if std < 1e-9:
        # Near-zero variance: deterministic
        return 1.0 if mean > line else 0.0

    # P(X > line) = 1 - CDF(line)
    return float(1.0 - norm.cdf(line, loc=mean, scale=std))


class PropAnalyzer:
    def __init__(
        self,
        season_weight: float = 0.4,
        recent_weight: float = 0.6,
        min_edge: float = 5.0,
        thresholds: dict | None = None,
    ):
        self.season_weight = season_weight
        self.recent_weight = recent_weight
        self.min_edge = min_edge
        self.thresholds = thresholds

    def analyze(
        self,
        prop: PlayerProp,
        season_avg: PlayerStat | None,
        recent_games: list[PlayerStat],
        opponent_def_rating: float | None = None,
        game_script: dict | None = None,
    ) -> PropAnalysis | None:
        # Validate line exists
        if prop.line is None:
            return None

        # Validate market is known
        fields = MARKET_STAT_MAP.get(prop.market)
        if fields is None:
            return None

        # Compute season average value for this market
        season_val: float | None = None
        if season_avg is not None:
            season_val = self._sum_fields(season_avg, fields)

        # Compute recent game-by-game values for this market
        recent_val: float | None = None
        game_values: list[float] = []
        if recent_games:
            game_vals = [self._sum_fields(g, fields) for g in recent_games]
            game_values = [v for v in game_vals if v is not None]
            if game_values:
                recent_val = sum(game_values) / len(game_values)

        # Need at least one source of stats
        if season_val is None and recent_val is None:
            return None

        # A variance estimate needs _MIN_VARIANCE_SAMPLES usable game-by-game
        # values. Without them the old code fell back to
        # abs(diff / line) * 100, which is not a probability: it divides by the
        # line, so a 0.5 line with a 1.2 projection reported a 140% "edge".
        # The distribution branch returns (prob - 0.5) * 200, bounded 0-100.
        # Both were fed to the same calculate_prop_confidence thresholds and
        # ranked against each other in the digest, so a 5-star from one did not
        # mean what a 5-star from the other meant. Refusing is the honest
        # option: fewer props, but one scale and one meaning for a star.
        if not _HAS_SCIPY or len(game_values) < _MIN_VARIANCE_SAMPLES:
            return None

        # Compute projection as weighted blend
        if season_val is not None and recent_val is not None:
            projection = self.season_weight * season_val + self.recent_weight * recent_val
        elif season_val is not None:
            projection = season_val
        else:
            projection = recent_val  # type: ignore[assignment]

        # Adjust projection based on opponent defensive rating
        if opponent_def_rating is not None:
            # League average defensive rating is ~110 for NBA, use 110 as baseline
            # Lower def_rating = better defense = reduce projection
            # Higher def_rating = worse defense = increase projection
            league_avg_def = 110.0
            def_factor = opponent_def_rating / league_avg_def
            projection = projection * def_factor

        # Adjust projection based on predicted game script
        if game_script is not None:
            predicted_diff = game_script.get("predicted_diff", 0)
            player_is_home = game_script.get("player_is_home", True)

            # Determine if the player's team is winning or losing in the predicted script
            player_winning = (player_is_home and predicted_diff > 0) or (not player_is_home and predicted_diff < 0)
            blowout_magnitude = abs(predicted_diff)

            # Only apply adjustments for significant predicted margins (> 7 points)
            if blowout_magnitude > 7:
                # Scale factor: larger predicted margin = larger adjustment
                # Cap at 15% adjustment for extreme blowouts
                scale = min(blowout_magnitude / 50, 0.15)

                market = prop.market
                if player_winning:
                    # Winning team in blowout: less passing (garbage time), more rushing (clock mgmt)
                    if market in ("player_pass_yards", "player_pass_yds"):
                        projection *= (1 - scale)
                    elif market in ("player_rush_yards", "player_rush_yds"):
                        projection *= (1 + scale * 0.5)
                    # NBA: winning team's stars play fewer minutes in blowout
                    elif market in ("player_points", "player_rebounds", "player_assists",
                                   "player_points_rebounds_assists", "player_points_rebounds",
                                   "player_points_assists"):
                        projection *= (1 - scale * 0.7)  # Stars sit in 4th quarter
                else:
                    # Losing team: more passing to catch up
                    if market in ("player_pass_yards", "player_pass_yds"):
                        projection *= (1 + scale)
                    elif market in ("player_rush_yards", "player_rush_yds"):
                        projection *= (1 - scale * 0.5)
                    # NBA: losing team's stars might also sit if it's a huge blowout
                    elif market in ("player_points", "player_rebounds", "player_assists"):
                        if blowout_magnitude > 15:
                            projection *= (1 - scale * 0.5)

        line = prop.line

        # --- Distribution-based edge computation ---
        # Exceedance probability P(X > line), centred on the weighted
        # projection with variance from the game-by-game values.
        mean_val = sum(game_values) / len(game_values)
        variance = sum((v - mean_val) ** 2 for v in game_values) / (len(game_values) - 1)

        exceedance_prob = _compute_exceedance_prob(
            mean=projection, variance=variance, line=line, market=prop.market
        )
        if prop.outcome == "Over":
            directional_prob = exceedance_prob
        else:
            directional_prob = 1.0 - exceedance_prob

        # 0.5 = no edge, 0.75 = 50% edge, 1.0 = 100% edge.
        edge_pct = (directional_prob - 0.5) * 200

        if edge_pct < 0:
            return None
        if edge_pct < self.min_edge:
            return None

        confidence = calculate_prop_confidence(edge_pct, self.thresholds)

        # Determine source/stale from season_avg or first recent game
        source = "unknown"
        is_stale = False
        if season_avg is not None:
            source = season_avg.source
            is_stale = bool(season_avg.is_stale)
        elif recent_games:
            source = recent_games[0].source
            is_stale = bool(recent_games[0].is_stale)

        return PropAnalysis(
            player_name=prop.player_name,
            market=prop.market,
            line=line,
            outcome=prop.outcome,
            season_avg=season_val,
            recent_avg=recent_val,
            projection=projection,
            edge_pct=round(edge_pct, 2),
            confidence=confidence,
            source=source,
            is_stale=is_stale,
            game_id=prop.game_id,
            odds=prop.odds,
            bookmaker=prop.bookmaker,
        )

    @staticmethod
    def _sum_fields(stat: PlayerStat, fields: list[str]) -> float | None:
        total = 0.0
        for f in fields:
            val = getattr(stat, f, None)
            if val is None:
                return None
            total += val
        return total
