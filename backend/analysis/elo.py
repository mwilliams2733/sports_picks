import math

class EloSystem:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self.ratings: dict[str, float] = {}

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))

    def update(self, home: str, away: str, winner: str) -> None:
        ra = self.get_rating(home)
        rb = self.get_rating(away)
        ea = self.expected_score(ra, rb)
        eb = 1.0 - ea
        sa = 1.0 if winner == home else 0.0
        sb = 1.0 - sa
        self.ratings[home] = ra + self.k_factor * (sa - ea)
        self.ratings[away] = rb + self.k_factor * (sb - eb)
