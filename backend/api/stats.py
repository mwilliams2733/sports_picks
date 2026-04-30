from fastapi import APIRouter, Request
from backend.database import get_session
from backend.models import PickModel, PickResult, Game
from backend.analysis.odds_utils import compute_pick_clv
from backend.analysis.recalibrator import EXPECTED_WIN_RATES

router = APIRouter()

@router.get("/record")
def get_record(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (session.query(PickResult, PickModel, Game)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id))
        if sport: query = query.filter(Game.sport == sport)
        rows = query.all()
        wins = sum(1 for r, _, _ in rows if r.result == "win")
        losses = sum(1 for r, _, _ in rows if r.result == "loss")
        pushes = sum(1 for r, _, _ in rows if r.result == "push")
        total = wins + losses
        total_profit = sum(r.payout for r, _, _ in rows)
        return {"wins": wins, "losses": losses, "pushes": pushes, "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / total * 100) if total > 0 else 0, 2),
            "total_profit": round(total_profit, 4)}
    finally:
        session.close()

@router.get("/daily")
def get_daily(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (session.query(Game.date, PickResult.result, PickResult.payout)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id))
        if sport: query = query.filter(Game.sport == sport)
        rows = query.order_by(Game.date).all()
        daily = {}
        for d, result, payout in rows:
            key = str(d)
            if key not in daily:
                daily[key] = {"date": key, "wins": 0, "losses": 0, "pushes": 0, "profit": 0.0}
            if result == "win": daily[key]["wins"] += 1
            elif result == "loss": daily[key]["losses"] += 1
            else: daily[key]["pushes"] += 1
            daily[key]["profit"] = round(daily[key]["profit"] + payout, 4)
        return list(daily.values())
    finally:
        session.close()

@router.get("/calibration")
def get_calibration(request: Request, sport: str | None = None):
    """Calibration data for a reliability diagram.

    Computed live from picks + pick_results grouped by confidence tier (1-5),
    not from calibration_history (which is biased toward broken-calibration
    moments since it only logs threshold adjustments). Returns per-tier
    predicted vs actual win rate plus an aggregate Brier score across all
    graded non-push picks.
    """
    session = get_session(request.app.state.engine)
    try:
        query = (
            session.query(PickModel.confidence, PickResult.result)
            .join(PickResult, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id)
        )
        if sport:
            query = query.filter(Game.sport == sport)
        rows = query.all()

        # Per-tier counts (skip pushes — they don't fit a binary calibration model).
        per_tier: dict[int, dict[str, int]] = {}
        squared_errors: list[float] = []
        for confidence, result in rows:
            if result == "push":
                continue
            bucket = per_tier.setdefault(confidence, {"wins": 0, "total": 0})
            bucket["total"] += 1
            if result == "win":
                bucket["wins"] += 1
            expected = EXPECTED_WIN_RATES.get(confidence, 0.5)
            outcome = 1.0 if result == "win" else 0.0
            squared_errors.append((expected - outcome) ** 2)

        tiers_out = []
        for tier in sorted(per_tier.keys(), reverse=True):
            b = per_tier[tier]
            actual = b["wins"] / b["total"] if b["total"] else 0.0
            tiers_out.append({
                "tier": tier,
                "predicted_win_rate": round(EXPECTED_WIN_RATES.get(tier, 0.5), 4),
                "actual_win_rate": round(actual, 4),
                "sample_size": b["total"],
            })

        brier = round(sum(squared_errors) / len(squared_errors), 4) if squared_errors else None
        return {
            "tiers": tiers_out,
            "total_graded": len(squared_errors),
            "brier_score": brier,
        }
    finally:
        session.close()


@router.get("/clv")
def get_clv_stats(request: Request):
    """Calculate Closing Line Value metrics.

    Price CLV (implied-probability delta) is meaningful for moneyline picks.
    Line CLV (points beaten) is meaningful for spread / over_under picks; it
    measures how many points better the line was at pick time vs. close.
    Both are reported separately because mixing them is nonsense.
    """
    session = get_session(request.app.state.engine)
    try:
        results = (
            session.query(PickResult, PickModel)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .all()
        )

        price_clv = []  # moneyline only
        line_clv = []   # spread + over_under

        for pr, pm in results:
            clv_pct, clv_points = compute_pick_clv(
                pm.pick_type, pm.pick_value,
                pm.odds_at_pick, pr.odds_at_close, pr.line_at_close,
            )
            if clv_pct is not None:
                price_clv.append(clv_pct)
            if clv_points is not None:
                line_clv.append(clv_points)

        def _summarize(values: list[float]) -> dict:
            if not values:
                return {"total_picks": 0, "clv_positive": 0, "clv_positive_pct": 0.0, "avg_clv": 0.0}
            positive = sum(1 for v in values if v > 0)
            return {
                "total_picks": len(values),
                "clv_positive": positive,
                "clv_positive_pct": round(positive / len(values) * 100, 1),
                "avg_clv": round(sum(values) / len(values), 2),
            }

        price_summary = _summarize(price_clv)
        line_summary = _summarize(line_clv)

        # Backwards compatibility: keep the original top-level keys reflecting
        # price CLV so existing frontend code does not break.
        return {
            **price_summary,
            "price_clv": price_summary,
            "line_clv": line_summary,
        }
    finally:
        session.close()
