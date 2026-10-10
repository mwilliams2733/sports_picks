"""The combat-sport Elo update, in one place: the grader applies it after each
bout and the calibration replay (combat_history) re-runs it over history."""


def actual_score(home_score, away_score) -> float:
    if home_score == away_score:
        return 0.5
    return 1.0 if (home_score or 0) > (away_score or 0) else 0.0


def elo_delta(home_rating: float, away_rating: float, actual_home: float, k: float) -> float:
    expected_home = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
    return k * (actual_home - expected_home)
