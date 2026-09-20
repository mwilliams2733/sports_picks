"""Does shrinking a team's rolling margin make the spread model less wrong?

`EnsembleStrategy._predicted_point_diff` predicts the home margin as
``home.point_diff - away.point_diff``: the difference of two rolling mean
margins, taken at face value however few games produced them. In NFL week 2
that yielded predicted margins of +39 and -45, cover probabilities of 0.9936
and 0.0002, and claimed edges of 50% -- every one of which `max_edge`
correctly refused, so the sport produced no spread picks at all.

The obvious repair is to regress each team's mean toward zero by how little
evidence it rests on:

    shrunk = point_diff * n / (n + k)

with ``n`` the team's prior completed games. ``k = 0`` is the current
behaviour. A large ``k`` predicts nothing, which is the baseline that
matters: if no finite ``k`` beats it, the rolling margin carries no signal
worth using and the honest move is to stop pretending it does.

This is a measurement, not a fix. It reports mean absolute error against the
realised margin for a sweep of ``k``, per sport, alongside two reference
points -- predicting zero, and predicting the market's own spread where one
was stored. Read it with the sample sizes and the date spread in view: a
sport whose games come from three Saturdays has nothing like as many
independent trials as its row count suggests.
"""
import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from backend.database import get_engine, get_session
from backend.models import Game, Odds, TeamStat
from backend.pipeline.team_stats import DEFAULT_LOOKBACK

DEFAULT_KS = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 1e9)


@dataclass
class Sample:
    """One finished game, with what a predictor could have known going in."""
    game_id: int
    date: str
    home_pd: float
    away_pd: float
    home_n: int
    away_n: int
    actual: float
    market: float | None = None


@dataclass
class Result:
    k: float
    mae: float
    n: int
    dates: int = 0
    teams: int = 0


def _point_diffs(session, sport: str) -> dict[tuple[int, int], float]:
    """{(game_id, team_id): point_diff} from the point-in-time stats table."""
    rows = (session.query(TeamStat.game_id, TeamStat.team_id, TeamStat.value)
            .join(Game, Game.id == TeamStat.game_id)
            .filter(Game.sport == sport, TeamStat.stat_type == "point_diff"))
    return {(g, t): v for g, t, v in rows}


def _market_spreads(session, sport: str) -> dict[int, float]:
    """{game_id: mean stored spread_home}. The line is the reference to beat."""
    by_game: dict[int, list[float]] = defaultdict(list)
    rows = (session.query(Odds.game_id, Odds.spread_home)
            .join(Game, Game.id == Odds.game_id)
            .filter(Game.sport == sport, Odds.spread_home.isnot(None)))
    for gid, spread in rows:
        by_game[gid].append(spread)
    return {g: statistics.mean(v) for g, v in by_game.items() if v}


def collect(session, sport: str) -> list[Sample]:
    """Every final game with a point-in-time margin for both teams.

    ``n`` is counted by replaying the sport chronologically and then capped
    at ``DEFAULT_LOOKBACK``, because that is exactly the window
    ``rolling_point_diff`` averaged over. Measuring against an uncapped count
    would tune ``k`` against evidence production never has: an nba team 60
    games into a season still has a mean built from 10, and a ``k`` fitted to
    60 would barely shrink it.
    """
    games = (session.query(Game)
             .filter(Game.sport == sport, Game.status == "final",
                     Game.home_score.isnot(None), Game.away_score.isnot(None))
             .order_by(Game.date.asc(), Game.id.asc()).all())
    pds = _point_diffs(session, sport)
    market = _market_spreads(session, sport)

    played: dict[int, int] = defaultdict(int)
    out: list[Sample] = []
    for g in games:
        hp = pds.get((g.id, g.home_team_id))
        ap = pds.get((g.id, g.away_team_id))
        if hp is not None and ap is not None:
            out.append(Sample(
                game_id=g.id, date=str(g.date), home_pd=hp, away_pd=ap,
                home_n=min(played[g.home_team_id], DEFAULT_LOOKBACK),
                away_n=min(played[g.away_team_id], DEFAULT_LOOKBACK),
                actual=float(g.home_score - g.away_score),
                # Stored as the home handicap: -3.5 means home favoured by
                # 3.5, so the implied margin is its negation.
                market=(-market[g.id] if g.id in market else None),
            ))
        played[g.home_team_id] += 1
        played[g.away_team_id] += 1
    return out


def shrink(point_diff: float, n: int, k: float) -> float:
    """Regress a rolling mean toward zero by how little evidence it rests on."""
    if k >= 1e9:
        return 0.0
    if n <= 0:
        return 0.0
    return point_diff * n / (n + k)


def evaluate(samples: list[Sample], ks=DEFAULT_KS) -> list[Result]:
    out = []
    for k in ks:
        errors = [abs((shrink(s.home_pd, s.home_n, k)
                       - shrink(s.away_pd, s.away_n, k)) - s.actual)
                  for s in samples]
        if not errors:
            continue
        out.append(Result(k=k, mae=statistics.mean(errors), n=len(errors),
                          dates=len({s.date for s in samples})))
    return out


def market_baseline(samples: list[Sample]) -> Result | None:
    """MAE of the market's own implied margin, on the games that have one."""
    errors = [abs(s.market - s.actual) for s in samples if s.market is not None]
    if not errors:
        return None
    dated = len({s.date for s in samples if s.market is not None})
    return Result(k=float("nan"), mae=statistics.mean(errors),
                  n=len(errors), dates=dated)


def format_report(sport: str, samples: list[Sample],
                  results: list[Result], market: Result | None) -> str:
    lines = [f"Margin shrinkage -- {sport}", ""]
    if not samples:
        lines.append("  no games with point-in-time margins for both teams")
        return "\n".join(lines)
    lines.append(f"  {len(samples)} games over {len({s.date for s in samples})} distinct dates")
    lines.append("")
    lines.append(f"  {'k':>8}  {'MAE':>7}  {'vs k=0':>8}")
    lines.append("  " + "-" * 28)
    base = next((r.mae for r in results if r.k == 0.0), None)
    for r in results:
        label = "inf (0)" if r.k >= 1e9 else f"{r.k:g}"
        delta = "" if base is None else f"{r.mae - base:+.3f}"
        lines.append(f"  {label:>8}  {r.mae:>7.3f}  {delta:>8}")
    lines.append("")
    if market is None:
        lines.append("  market line: no stored spread for any of these games,")
        lines.append("  so 'beats the line' is NOT measurable here.")
    else:
        lines.append(f"  market line MAE: {market.mae:.3f} "
                     f"(n={market.n}, {market.dates} dates)")
        lines.append("  The line is the number to beat; MAE alone only says")
        lines.append("  which prediction is less wrong, not which is profitable.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport", action="append", dest="sports")
    args = ap.parse_args(argv)

    session = get_session(get_engine(args.db))
    try:
        for sport in (args.sports or ["nfl", "ncaaf", "mlb", "ncaab", "nba"]):
            samples = collect(session, sport)
            print(format_report(sport, samples, evaluate(samples),
                                market_baseline(samples)))
            print()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
