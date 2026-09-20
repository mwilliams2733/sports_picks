"""Sport-specific empirical constants for CDF-based edge calculations."""

# Standard deviation of game-to-game scoring margin (home_score - away_score)
POINT_DIFF_STD = {
    "nba": 12.0,
    "nfl": 13.5,
    "ncaab": 11.0,
    "ncaaf": 17.0,
    "boxing": 12.0,
    "mma": 12.0,
    "mlb": 3.5,
}

# Standard deviation of game-to-game total points
#: Standard deviation of the totals model's own residuals, which is what the
#: over/under CDF needs -- not the spread of final totals in general.
#:
#: nba, ncaab and mlb are measured out-of-sample by
#: ``backend.analysis.totals_report`` on 2026-09-19, for the model as it is
#: actually configured -- with the opponent adjustment on, which tightens
#: the residuals slightly. Each is still LARGER than the value assumed
#: before anything was measured (nba 15.0 -> 19.5, ncaab 12.0 -> 13.4), and
#: a too-small value makes the model overconfident and inflates every edge
#: it claims. Re-measure whenever a switch in ensemble.py changes.
#:
#: The rest are unmeasured guesses; ncaab's rests on only 22 games. Re-run
#: the report and update them as history accumulates.
TOTAL_POINTS_STD = {
    "nba": 19.5,     # measured on the adjusted model, n=1229
    "nfl": 13.0,     # unmeasured
    "ncaab": 13.4,   # measured, n=22 -- thin
    "ncaaf": 16.0,   # unmeasured
    "boxing": 15.0,  # unmeasured
    "mma": 15.0,     # unmeasured
    "mlb": 3.7,      # measured, n=79
}

# Home court/field advantage in ELO points
HOME_ADVANTAGE_ELO = {
    "nba": 100,
    "nfl": 48,
    "ncaab": 120,
    "ncaaf": 65,
    "boxing": 0,
    "mma": 0,
    "mlb": 24,
}

# Default home win probability (used as HCA weight in fallback models)
HOME_WIN_RATE = {
    "nba": 0.60,
    "nfl": 0.57,
    "ncaab": 0.67,
    "ncaaf": 0.62,
    "boxing": 0.50,
    "mma": 0.50,
    "mlb": 0.54,
}


def get_point_diff_std(sport: str) -> float:
    return POINT_DIFF_STD.get(sport, 12.0)


#: Measured mean error of the totals model by (sport, season phase), in
#: points of total. Subtracted from the prediction.
#:
#: Only entries that were measured appear. nba postseason scores far less
#: than the regular season -- 210.5 against 231.1 -- and the model, fitted
#: on regular-season scoring rates, misses those games by -17.09 on average
#: (n=21, sd 20.6, t = -3.80).
#:
#: This is ONE postseason of evidence and the interval is wide, roughly
#: -26.5 to -7.7. The direction is not in doubt; the magnitude is. Re-measure
#: with `backend.analysis.totals_report` as playoffs accumulate.
#:
#: Deliberately keyed on (sport, phase) rather than phase alone: ncaab
#: postseason shows no bias at all (-1.58, t = -0.50, n=20), because a
#: single-elimination tournament is played at roughly regular-season pace
#: while an nba playoff series is not. A global playoff correction would
#: have been wrong for it.
TOTAL_BIAS_BY_PHASE = {
    ("nba", "postseason"): -17.1,
}


def get_total_bias(sport: str, season_type: str) -> float:
    """Measured bias for this sport and phase, or 0.0 when unmeasured."""
    return TOTAL_BIAS_BY_PHASE.get((sport, season_type), 0.0)


def get_total_points_std(sport: str) -> float:
    return TOTAL_POINTS_STD.get(sport, 15.0)


def get_home_advantage_elo(sport: str) -> int:
    return HOME_ADVANTAGE_ELO.get(sport, 100)


def get_home_win_rate(sport: str) -> float:
    return HOME_WIN_RATE.get(sport, 0.57)
