"""How well does the totals model predict, and does it beat the line?

Two different questions, and only the second decides whether to bet.

`_predicted_total` is built from points-for / points-against averages that
are point-in-time by construction (`team_stats.strictly_before`), so scoring
a stored final game from its own stored stats is a genuine out-of-sample
test -- the game's own result cannot have reached its own features.

Beating a constant is not skill. The previous formula returned 200.0 for
every game in every sport, so *any* real predictor improves on it hugely;
the column is printed only to keep that improvement from being mistaken for
an edge. The bet is against the market's number, so the market's own error
on the same games is the bar.

    python -m backend.analysis.totals_report --db <abs path>
"""
import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass

from backend.database import get_engine, get_session
from backend.models import Game, Odds, TeamStat

#: The prediction the model made before points_for existed, kept as a
#: reference column. See the module docstring.
LEGACY_CONSTANT_TOTAL = 200.0


@dataclass
class SportResult:
    sport: str
    n: int
    mae: float
    residual_sd: float
    bias: float
    legacy_mae: float
    n_with_line: int = 0
    mae_vs_line: float | None = None
    line_mae: float | None = None

    @property
    def beats_line(self) -> bool | None:
        if self.line_mae is None or self.mae_vs_line is None:
            return None
        return self.mae_vs_line < self.line_mae


def _scoring_stats(session) -> dict[tuple[int, int], dict[str, float]]:
    out: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    for row in session.query(TeamStat).filter(
            TeamStat.stat_type.in_(("points_for", "points_against"))):
        out[(row.game_id, row.team_id)][row.stat_type] = row.value
    return out


def predicted_total(home: dict, away: dict) -> float | None:
    """The matchup average, or None when either side lacks a history."""
    if len(home) < 2 or len(away) < 2:
        return None
    return ((home["points_for"] + away["points_against"]) / 2
            + (away["points_for"] + home["points_against"]) / 2)


def evaluate(session) -> list[SportResult]:
    stats = _scoring_stats(session)
    lines: dict[int, list[float]] = defaultdict(list)
    for o in session.query(Odds).filter(Odds.over_under.isnot(None)):
        lines[o.game_id].append(o.over_under)

    rows: dict[str, list[tuple[float, float, float | None]]] = defaultdict(list)
    for g in session.query(Game).filter(Game.status == "final",
                                        Game.home_score.isnot(None),
                                        Game.away_score.isnot(None)):
        pred = predicted_total(stats.get((g.id, g.home_team_id), {}),
                               stats.get((g.id, g.away_team_id), {}))
        if pred is None:
            continue
        line = statistics.mean(lines[g.id]) if g.id in lines else None
        rows[g.sport].append((pred, float(g.home_score + g.away_score), line))

    results = []
    for sport, data in rows.items():
        residuals = [actual - pred for pred, actual, _ in data]
        withline = [(p, a, ln) for p, a, ln in data if ln is not None]
        r = SportResult(
            sport=sport,
            n=len(data),
            mae=statistics.mean(abs(x) for x in residuals),
            residual_sd=statistics.pstdev(residuals) if len(residuals) > 1 else 0.0,
            bias=statistics.mean(residuals),
            legacy_mae=statistics.mean(
                abs(a - LEGACY_CONSTANT_TOTAL) for _, a, _ in data),
            n_with_line=len(withline),
        )
        if withline:
            r.mae_vs_line = statistics.mean(abs(a - p) for p, a, _ in withline)
            r.line_mae = statistics.mean(abs(a - ln) for _, a, ln in withline)
        results.append(r)
    return sorted(results, key=lambda r: -r.n)


def format_report(results: list[SportResult]) -> str:
    lines = ["Totals model -- out-of-sample against stored finals", ""]
    lines.append(f"  {'sport':<7} {'n':>6} {'MAE':>7} {'resid sd':>9} "
                 f"{'bias':>8} {'legacy MAE':>11}")
    lines.append("  " + "-" * 52)
    for r in results:
        lines.append(f"  {r.sport:<7} {r.n:>6} {r.mae:>7.2f} "
                     f"{r.residual_sd:>9.2f} {r.bias:>+8.2f} {r.legacy_mae:>11.2f}")
    lines.append("")
    lines.append("  legacy MAE is the old constant-200 prediction. Beating it")
    lines.append("  is not skill -- any real predictor does.")
    lines.append("")
    lines.append("  Against the market line, which is what a bet is against:")
    lines.append(f"  {'sport':<7} {'n':>6} {'our MAE':>9} {'line MAE':>9} {'beats line':>11}")
    lines.append("  " + "-" * 50)
    for r in results:
        if r.line_mae is None:
            lines.append(f"  {r.sport:<7} {0:>6}  no priced games")
            continue
        verdict = "YES" if r.beats_line else "no"
        lines.append(f"  {r.sport:<7} {r.n_with_line:>6} {r.mae_vs_line:>9.2f} "
                     f"{r.line_mae:>9.2f} {verdict:>11}")
    lines.append("")
    lines.append("  residual sd is what the over/under CDF should use; a smaller")
    lines.append("  assumed value makes the model overconfident and inflates edge.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    args = ap.parse_args(argv)
    session = get_session(get_engine(args.db))
    try:
        print(format_report(evaluate(session)))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
