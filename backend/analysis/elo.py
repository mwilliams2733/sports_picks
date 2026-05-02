import math

K_FACTORS = {
    "nba": 32, "nfl": 32, "ncaab": 32, "ncaaf": 32, "mlb": 32,
    "mma": 24, "boxing": 24,
}


def get_k_factor(sport: str) -> int:
    """Return the Elo K-factor for a given sport.

    Team sports use K=32. Combat sports (MMA, boxing) use K=24 because
    fighters compete 2-4x/year vs. 80x for NBA — each fight carries more
    information per match but cumulative data is sparser.

    NOTE: This helper is consumed by the combat-sports grader path only.
    The legacy team-sport Elo path uses ``EloSystem(k_factor=20)`` (see
    ``backend/backtesting/historical.py``); do not route team sports through
    this function without an explicit recalibration decision — doing so
    would silently shift live team-sport Elo updates from K=20 to K=32.
    """
    return K_FACTORS.get(sport, 32)


class EloSystem:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0,
                 home_advantage: float = 0.0):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self.home_advantage = home_advantage
        self.ratings: dict[str, float] = {}

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def expected_score(self, rating_a: float, rating_b: float,
                       home_advantage: float = 0.0) -> float:
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a - home_advantage) / 400.0))

    def _mov_multiplier(self, margin: int, elo_diff: float) -> float:
        """Margin-of-victory multiplier with autocorrelation correction.

        FiveThirtyEight-style: log(abs(margin)+1) * 2.2 / (elo_diff*0.001 + 2.2)
        """
        abs_margin = abs(margin)
        log_factor = math.log(abs_margin + 1)
        correction = 2.2 / (max(elo_diff, 0) * 0.001 + 2.2)
        return log_factor * correction

    def update(self, home: str, away: str, winner: str,
               margin: int | None = None) -> None:
        ra = self.get_rating(home)
        rb = self.get_rating(away)
        ea = self.expected_score(ra, rb, home_advantage=self.home_advantage)
        eb = 1.0 - ea
        sa = 1.0 if winner == home else 0.0
        sb = 1.0 - sa

        if margin is not None and margin != 0:
            winner_rating = ra if winner == home else rb
            loser_rating = rb if winner == home else ra
            elo_diff = winner_rating - loser_rating
            mov = self._mov_multiplier(margin, elo_diff)
        else:
            mov = 1.0

        k = self.k_factor * mov
        self.ratings[home] = ra + k * (sa - ea)
        self.ratings[away] = rb + k * (sb - eb)
