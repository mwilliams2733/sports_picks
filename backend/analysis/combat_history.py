"""Leak-free replay of combat history for calibration.

For every final bout, in (date, id) order, the features the live pick
generator would have seen BEFORE it (`pick_generator._build_fighter_stats`):
current Elo, last-n form (wins / bouts; a draw is not a win), the CURRENT
rating of the last-n opponents, and the bout count capped at n. Elo is
updated after the features are taken, with the grader's arithmetic
(`combat_elo`), so no bout's result reaches its own features.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date

from backend.analysis.combat_elo import actual_score, elo_delta
from backend.models import Game

SEED = 1500.0


@dataclass(frozen=True)
class Bout:
    game_id: int
    date: date
    a: int
    b: int
    a_score: float           # 1 win, 0 loss, 0.5 draw -- for fighter a


@dataclass(frozen=True)
class BoutFeatures:
    game_id: int
    date: date
    a: int
    b: int
    elo_a: float
    elo_b: float
    form_a: float
    form_b: float
    quality_a: float | None
    quality_b: float | None
    fights_a: int
    fights_b: int
    outcome: float


def load_bouts(session, sport: str = "mma") -> list[Bout]:
    rows = (session.query(Game)
            .filter(Game.sport == sport, Game.status == "final",
                    Game.home_score.isnot(None), Game.away_score.isnot(None))
            .order_by(Game.date, Game.id).all())
    return [Bout(g.id, g.date, g.home_team_id, g.away_team_id,
                 actual_score(g.home_score, g.away_score)) for g in rows]


def replay(bouts: list[Bout], k: float, last_n: int = 5) -> list[BoutFeatures]:
    rating: dict[int, float] = defaultdict(lambda: SEED)
    # Most recent first, like the live builder's order_by(date desc).limit(n).
    recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=last_n))   # (won, opponent)
    out: list[BoutFeatures] = []

    def side(fid: int) -> tuple[float, float, float | None, int]:
        past = recent[fid]
        if not past:
            return rating[fid], 0.5, None, 0
        wins = sum(1 for won, _ in past if won)
        quality = sum(rating[opp] for _, opp in past) / len(past)
        return rating[fid], wins / len(past), quality, len(past)

    for bout in bouts:
        ea, fa, qa, na = side(bout.a)
        eb, fb, qb, nb = side(bout.b)
        out.append(BoutFeatures(bout.game_id, bout.date, bout.a, bout.b, ea, eb, fa, fb,
                                qa, qb, na, nb, bout.a_score))
        delta = elo_delta(rating[bout.a], rating[bout.b], bout.a_score, k)
        rating[bout.a] += delta
        rating[bout.b] -= delta
        recent[bout.a].appendleft((bout.a_score == 1.0, bout.b))
        recent[bout.b].appendleft((bout.a_score == 0.0, bout.a))
    return out
