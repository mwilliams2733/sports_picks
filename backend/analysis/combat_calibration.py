"""Fit and measure the MMA win-probability model on history (no leakage).

Symmetric by construction: difference features, no intercept, every bout
fitted as stored AND mirrored -- UFCStats lists the winner first (the
first-listed fighter won 64% of imported bouts), so any corner term would
learn the CSV's ordering, not fighting.

Time split: fit < fit_end, choose K on [fit_end, val_end), report >= val_end
once. Only bouts the live model would price: both fighters with >= 1 earlier
bout, decided (draws still move Elo, as 0.5, in the replay).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date

import numpy as np
from sklearn.linear_model import LogisticRegression

from backend.analysis.combat_history import BoutFeatures, load_bouts, replay
from backend.data_types import FighterStats

FEATURES = ("elo_diff", "form_diff", "quality_diff", "experience_diff")
BASELINE_K = 24


def design(f: BoutFeatures) -> np.ndarray:
    quality = 0.0 if f.quality_a is None or f.quality_b is None else (f.quality_a - f.quality_b) / 400
    return np.array([(f.elo_a - f.elo_b) / 400, f.form_a - f.form_b, quality,
                     math.log1p(f.fights_a) - math.log1p(f.fights_b)])


def eligible(f: BoutFeatures) -> bool:
    return f.fights_a > 0 and f.fights_b > 0 and f.outcome in (0.0, 1.0)


def fit(features: list[BoutFeatures]) -> np.ndarray:
    X = np.array([design(f) for f in features])
    y = np.array([f.outcome for f in features])
    model = LogisticRegression(fit_intercept=False, C=1.0, max_iter=1000)
    model.fit(np.vstack([X, -X]), np.concatenate([y, 1 - y]))
    return model.coef_[0]


def probability(coef: np.ndarray, f: BoutFeatures) -> float:
    return float(1 / (1 + math.exp(-float(design(f) @ coef))))


def _stats(elo, form, quality, n) -> FighterStats:
    return FighterStats(elo_rating=elo, recent_form_score=form, opponent_avg_elo=quality,
                        fights_count=n, days_since_last_fight=None)


def baseline_probability(f: BoutFeatures) -> float:
    """The live blend (CombatSportsStrategy._model_probability), called, not copied."""
    from backend.analysis.variants.combat_sports import CombatSportsStrategy
    strat = CombatSportsStrategy(name="baseline", config={})
    return strat._model_probability(_stats(f.elo_a, f.form_a, f.quality_a, f.fights_a),
                                    _stats(f.elo_b, f.form_b, f.quality_b, f.fights_b))


def scores(probs: list[float], outcomes: list[float]) -> dict:
    p = np.clip(np.array(probs), 1e-6, 1 - 1e-6)
    y = np.array(outcomes)
    return {"n": int(len(y)),
            "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "brier": round(float(np.mean((p - y) ** 2)), 4),
            "mean_abs_from_half": round(float(np.mean(np.abs(p - 0.5))), 4)}


def _window(features, start, end):
    return [f for f in features if eligible(f) and (start is None or f.date >= start)
            and (end is None or f.date < end)]


def evaluate(session, ks=(16, 24, 32, 48, 64, 96), fit_end=date(2021, 1, 1),
             val_end=date(2024, 1, 1)) -> dict:
    bouts = load_bouts(session, "mma")
    by_k = {}
    for k in ks:
        feats = replay(bouts, k)
        train, val = _window(feats, None, fit_end), _window(feats, fit_end, val_end)
        coef = fit(train)
        by_k[k] = {"coef": coef, "feats": feats,
                   "val": scores([probability(coef, f) for f in val], [f.outcome for f in val])}
    best = min(ks, key=lambda k: by_k[k]["val"]["log_loss"])
    # Refit on everything before the test window with the chosen K, then score
    # the test window ONCE.
    feats = by_k[best]["feats"]
    coef = fit(_window(feats, None, val_end))
    test = _window(feats, val_end, None)
    base_feats = by_k[BASELINE_K]["feats"] if BASELINE_K in by_k else replay(bouts, BASELINE_K)
    base_test = _window(base_feats, val_end, None)
    return {
        "bouts": len(bouts), "chosen_k": best, "features": list(FEATURES),
        "coef": [round(float(c), 4) for c in coef],
        "validation_by_k": {k: by_k[k]["val"] for k in ks},
        "test_new": scores([probability(coef, f) for f in test], [f.outcome for f in test]),
        "test_baseline_k24_blend": scores([baseline_probability(f) for f in base_test],
                                          [f.outcome for f in base_test]),
        "windows": {"fit_end": str(fit_end), "val_end": str(val_end)},
    }


def market_report(session, coef, k: float, min_edge: float) -> dict:
    """The fitted model vs the books on MMA finals that have prices, and the
    picks the strategy's rule would have made from it. n is small and bouts
    on one card are correlated: this can show the underdog pattern, not an edge.
    Prices are averaged with `strategy.average_odds`, as the strategy does.

    Prices are PRE-FIGHT only (final review, 2026-10-10): the Odds table keeps
    each book's latest price, which for MMA can be in-play or settled (games
    from the odds feed have no start time, so nothing stops a fight-day fetch).
    Each book's price is the last line snapshot captured before 00:00 UTC on
    the bout date -- the evening before in ET, before any card starts. A bout
    with no such price is left out."""
    from collections import defaultdict
    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig, value_edge
    from backend.analysis.strategy import average_odds
    from backend.data_types import OddsSnapshot
    from datetime import datetime, time
    from backend.models import Game, LineSnapshot

    feats = {f.game_id: f for f in replay(load_bouts(session, "mma"), k)}
    latest: dict[tuple[int, str], LineSnapshot] = {}
    for snap, game_date in (session.query(LineSnapshot, Game.date)
                            .join(Game, Game.id == LineSnapshot.game_id)
                            .filter(Game.sport == "mma", Game.status == "final")
                            .order_by(LineSnapshot.captured_at)):
        cutoff = datetime.combine(game_date, time(0, 0))
        captured = snap.captured_at.replace(tzinfo=None)
        if captured < cutoff and snap.moneyline_home and snap.moneyline_away:
            latest[(snap.game_id, snap.bookmaker)] = snap      # later wins: price at cutoff
    by_game = defaultdict(list)
    for (gid, _), snap in latest.items():
        by_game[gid].append(snap)
    model_p, market_p, ys = [], [], []
    picks = dogs = 0
    on_picks_model, on_picks_market = [], []
    for gid in sorted(by_game):
        f = feats.get(gid)
        if f is None or not eligible(f):
            continue
        rows = by_game[gid]
        nv = [remove_vig(american_to_implied_prob(o.moneyline_home),
                         american_to_implied_prob(o.moneyline_away))[0] for o in rows]
        m, p = sum(nv) / len(nv), probability(coef, f)
        model_p.append(p)
        market_p.append(m)
        ys.append(f.outcome)
        avg = average_odds([OddsSnapshot(bookmaker=o.bookmaker, moneyline_home=o.moneyline_home,
                                         moneyline_away=o.moneyline_away, spread_home=0.0,
                                         spread_away=0.0, over_under=0.0) for o in rows])
        if avg is None:
            continue
        for prob, price, mk in ((p, avg["moneyline_home"], m), (1 - p, avg["moneyline_away"], 1 - m)):
            if value_edge(prob, price) >= min_edge:      # home first, as predict() does
                picks += 1
                dogs += int(price > 0)
                on_picks_model.append(prob)
                on_picks_market.append(mk)
                break
    return {"n": len(ys), "model": scores(model_p, ys), "market": scores(market_p, ys),
            "market_probs": market_p, "simulated_picks": picks,
            "underdog_share": round(dogs / picks, 4) if picks else None,
            "mean_model_on_picks": round(float(np.mean(on_picks_model)), 4) if picks else None,
            "mean_market_on_picks": round(float(np.mean(on_picks_market)), 4) if picks else None}


def main(argv: list[str]) -> int:
    from backend.config import load_config
    from backend.database import get_engine, get_session
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    db = args.db or load_config("config.yaml")["database_path"]
    session = get_session(get_engine(db))
    try:
        print(json.dumps(evaluate(session), indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
