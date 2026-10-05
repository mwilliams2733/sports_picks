"""How much of a team's Elo should survive the off-season?

Production replays Elo game by game across seasons with no reset
(`team_stats.backfill_elo_history`), so opening night is rated on last
season's final form although rosters have changed. The standard fix pulls
each rating part of the way back to the mean at a team's first game of a
new season:

    rating = 1500 + carry * (rating - 1500)

carry = 1.0 is today's behaviour. This replays production's exact system
(same EloSystem, K, home advantage, margin multiplier, game list and
order, and the same `elo.season_carry`) for each
carry and scores the pre-game home-win probability on every game of every
season after the first -- the first season starts from the 1500 seed for
everyone, so it has nothing to regress from. Scored two ways: the first
EARLY_GAMES games of each team's season (where the reset matters) and the
whole season.

Ties are skipped, as the replay skips them. Read only:

    python -m backend.scripts.elo_carryover_experiment --db <snapshot> --sport nfl
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict

from backend.analysis.elo import EloSystem, apply_result, season_carry
from backend.analysis.sport_constants import get_home_advantage_elo
from backend.database import get_engine, get_session
from backend.models import Team
from backend.pipeline.team_stats import ELO_K_FACTOR, _final_games

CARRIES = (1.0, 0.9, 0.8, 0.75, 0.67, 0.6, 0.5, 0.33)
EARLY_GAMES = {"nfl": 4, "nba": 15}


def replay(games, abbr, sport: str, carry: float):
    """[(season, early?, p_home, home_won)] for every decided game."""
    elo = EloSystem(k_factor=ELO_K_FACTOR, home_advantage=get_home_advantage_elo(sport))
    last_season: dict[str, str] = {}
    played: dict[tuple[str, str], int] = defaultdict(int)
    out = []
    for g in games:
        h, a = abbr[g.home_team_id], abbr[g.away_team_id]
        for t in (h, a):
            if t in last_season and last_season[t] != g.season and t in elo.ratings:
                elo.ratings[t] = season_carry(elo.get_rating(t), carry)
            last_season[t] = g.season
        p = elo.expected_score(elo.get_rating(h), elo.get_rating(a), home_advantage=elo.home_advantage)
        early = min(played[(g.season, h)], played[(g.season, a)]) < EARLY_GAMES[sport]
        played[(g.season, h)] += 1
        played[(g.season, a)] += 1
        if g.home_score != g.away_score:
            out.append((g.season, early, p, 1.0 if g.home_score > g.away_score else 0.0))
        apply_result(elo, h, a, g.home_score, g.away_score)
    return out


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def refit(rows, scored):
    """Leave-one-season-out: fit y ~ a + b*logit(p) on the other seasons and
    predict the held-out one. The model consumes Elo through a fitted
    regression, so raw Elo calibration is not what matters -- whether the
    rating carries information is. Returns [(early, p_refit, y)]."""
    import numpy as np
    out = []
    for held in scored:
        train = [(r[2], r[3]) for r in rows if r[0] != held]
        if not train:
            # Only one scored season (nba: the db holds two, and the first is
            # the warm-up). Fit on its LATE games and score only its early
            # ones, which the fit never sees.
            train = [(r[2], r[3]) for r in rows if r[0] == held and not r[1]]
            rows = [r for r in rows if not (r[0] == held and not r[1])]
        x = np.array([[1.0, _logit(p)] for p, _ in train])
        y = np.array([yy for _, yy in train])
        w = np.zeros(2)
        for _ in range(25):  # Newton on the logistic log-likelihood
            mu = 1 / (1 + np.exp(-x @ w))
            grad = x.T @ (y - mu)
            hess = x.T @ (x * (mu * (1 - mu))[:, None])
            w += np.linalg.solve(hess, grad)
        for s, early, p, yy in rows:
            if s == held:
                out.append((early, float(1 / (1 + math.exp(-(w[0] + w[1] * _logit(p))))), yy))
    return out


def auc(pairs) -> float:
    """Rank-based: P(a home win got a higher p than a home loss). Scale-free."""
    pos = [p for p, y in pairs if y == 1.0]
    neg = [p for p, y in pairs if y == 0.0]
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def scores(rows):
    n = len(rows)
    brier = sum((p - y) ** 2 for p, y in rows) / n
    ll = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in rows) / n
    return n, brier, ll


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport", required=True, choices=sorted(EARLY_GAMES))
    args = ap.parse_args(argv)
    session = get_session(get_engine(args.db))
    try:
        # Exactly the games production replays (`_final_games`), all-star
        # games included, so this measures the system that ships.
        games = _final_games(session, args.sport)
        abbr = {t.id: t.abbreviation for t in session.query(Team).filter(Team.sport == args.sport)}
    finally:
        session.close()
    seasons = sorted({g.season for g in games})
    scored = seasons[1:]
    print(f"{args.sport}: {len(games)} games, seasons {seasons}; scoring {scored}")
    print(f"early = either team within its first {EARLY_GAMES[args.sport]} games of the season\n")
    print(f"{'carry':>6} | {'early n':>7} {'Brier':>7} {'logloss':>8} | "
          f"{'season n':>8} {'Brier':>7} {'logloss':>8} | early Brier by season")
    base = None
    refits = []
    for carry in CARRIES:
        rows = [r for r in replay(games, abbr, args.sport, carry) if r[0] in scored]
        e = scores([(p, y) for s, early, p, y in rows if early])
        a = scores([(p, y) for s, early, p, y in rows])
        per = "  ".join(f"{s[:4]} {scores([(p, y) for ss, early, p, y in rows if early and ss == s])[1]:.4f}"
                        for s in scored if any(r[0] == s and r[1] for r in rows))
        base = base or (e, a)
        print(f"{carry:>6.2f} | {e[0]:>7} {e[1]:.4f} {e[2]:>8.4f} | {a[0]:>8} {a[1]:.4f} {a[2]:>8.4f} | {per}")
        refits.append((carry, refit(rows, scored), rows))
    print("\nRESCALED (what the model sees): y ~ a + b*logit(p), fitted leave-one-season-out")
    print(f"{'carry':>6} | {'early Brier':>11} {'logloss':>8} {'AUC':>6} | "
          f"{'season Brier':>12} {'logloss':>8} {'AUC':>6}")
    for carry, rf, rows in refits:
        e = scores([(p, y) for early, p, y in rf if early])
        a = scores([(p, y) for early, p, y in rf])
        print(f"{carry:>6.2f} | {e[1]:>11.4f} {e[2]:>8.4f} "
              f"{auc([(p, y) for s, early, p, y in rows if early]):>6.4f} | "
              f"{a[1]:>12.4f} {a[2]:>8.4f} {auc([(p, y) for s, early, p, y in rows]):>6.4f}")
    print("\nLower is better. Differences of a few 0.001 Brier on a few hundred early games"
          "\nare inside noise: each team plays every week, so games are not independent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
