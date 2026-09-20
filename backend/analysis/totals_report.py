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
    #: The same MAE using each team's venue-specific rate where available.
    n_split: int = 0
    split_mae: float | None = None
    blended_mae_same_games: float | None = None

    @property
    def splits_help(self) -> bool | None:
        if self.split_mae is None or self.blended_mae_same_games is None:
            return None
        return self.split_mae < self.blended_mae_same_games

    @property
    def beats_line(self) -> bool | None:
        if self.line_mae is None or self.mae_vs_line is None:
            return None
        return self.mae_vs_line < self.line_mae


SCORING_STATS = (
    "points_for", "points_against",
    "points_for_home", "points_against_home",
    "points_for_away", "points_against_away",
)


def _scoring_stats(session) -> dict[tuple[int, int], dict[str, float]]:
    out: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    for row in session.query(TeamStat).filter(
            TeamStat.stat_type.in_(SCORING_STATS)):
        out[(row.game_id, row.team_id)][row.stat_type] = row.value
    return out


def split_total(home: dict, away: dict) -> float | None:
    """The venue-split prediction, or None when either side lacks its split.

    Compared against the blended prediction on exactly the same games -- a
    split model that only covers the well-sampled matchups would otherwise
    look better for reasons that have nothing to do with venue.
    """
    keys = ("points_for_home", "points_against_home")
    akeys = ("points_for_away", "points_against_away")
    if not all(k in home for k in keys) or not all(k in away for k in akeys):
        return None
    return ((home["points_for_home"] + home["points_against_home"])
            + (away["points_for_away"] + away["points_against_away"])) / 2


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

    rows: dict[str, list[tuple[float, float, float | None, float | None]]] = defaultdict(list)
    for g in session.query(Game).filter(Game.status == "final",
                                        Game.home_score.isnot(None),
                                        Game.away_score.isnot(None)):
        pred = predicted_total(stats.get((g.id, g.home_team_id), {}),
                               stats.get((g.id, g.away_team_id), {}))
        if pred is None:
            continue
        line = statistics.mean(lines[g.id]) if g.id in lines else None
        split = None if g.neutral_site else split_total(
            stats.get((g.id, g.home_team_id), {}),
            stats.get((g.id, g.away_team_id), {}))
        rows[g.sport].append(
            (pred, float(g.home_score + g.away_score), line, split))

    results = []
    for sport, data in rows.items():
        residuals = [actual - pred for pred, actual, _, _ in data]
        withline = [(p, a, ln) for p, a, ln, _ in data if ln is not None]
        withsplit = [(p, a, sp) for p, a, _, sp in data if sp is not None]
        r = SportResult(
            sport=sport,
            n=len(data),
            mae=statistics.mean(abs(x) for x in residuals),
            residual_sd=statistics.pstdev(residuals) if len(residuals) > 1 else 0.0,
            bias=statistics.mean(residuals),
            legacy_mae=statistics.mean(
                abs(a - LEGACY_CONSTANT_TOTAL) for _, a, _, _ in data),
            n_with_line=len(withline),
            n_split=len(withsplit),
        )
        if withsplit:
            r.split_mae = statistics.mean(abs(a - sp) for _, a, sp in withsplit)
            r.blended_mae_same_games = statistics.mean(
                abs(a - p) for p, a, _ in withsplit)
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
    lines.append("  Venue splits vs one blended rate, on the same games:")
    lines.append(f"  {'sport':<7} {'n':>6} {'split MAE':>10} {'blended':>9} "
                 f"{'splits help':>12}")
    lines.append("  " + "-" * 50)
    for r in results:
        if r.split_mae is None:
            lines.append(f"  {r.sport:<7} {r.n_split:>6}  no game has both splits")
            continue
        lines.append(f"  {r.sport:<7} {r.n_split:>6} {r.split_mae:>10.2f} "
                     f"{r.blended_mae_same_games:>9.2f} "
                     f"{('YES' if r.splits_help else 'no'):>12}")
    lines.append("")
    lines.append("  Compared on identical games, so a split model that only")
    lines.append("  covers well-sampled matchups cannot look better for that")
    lines.append("  reason alone.")
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
