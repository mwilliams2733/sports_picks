"""Does turnover margin know anything the closing spread does not?

Why this exists
---------------
The owner asked (2026-10-04) whether picks consider turnovers. The live NFL
model (`ensemble` -> `CalibratedModel`) has no turnover input. The inactive
`SportSpecificStrategy` gives turnover margin 20% of its NFL weight, but
nothing ever stores `turnover_margin`, so that slot is a constant 0.5. Before
wiring turnovers into anything, this asks the question the EPA experiment
asked (`backend.scripts.epa_experiment`), with that harness's own tests:

**S. Stickiness.** Correlate each team-season's per-game figure over weeks
1-9 with weeks 10-18. A feature that does not persist within a season cannot
forecast the next game, whatever it explains about the last one.

**B. Incremental information.** `margin = a + b*line + c*feature`, over
every joined game. The feature is used raw (no fitted scale), so there is
nothing to leak and no split is needed for this test. `c` is the answer.

**C. Betting the disagreement.** The points-per-unit scale is fitted on the
training seasons as `margin - line ~ k * feature`, frozen, and the ATS
record of `line + k * feature` is read on the test seasons against -110
break-even.

Features, each home minus away, walk-forward within the season (game N uses
that team's games 1..N-1; a team's first game of a season is 0):

* `to_margin`  -- takeaways minus giveaways per game.
* `giveaways`  -- giveaways per game, sign flipped so positive favours home.
  Ball security is the half of turnover margin usually argued to be skill.

Turnovers are interceptions (charged to the passing team) plus lost fumbles
charged to `fumbled_1_team`, not `posteam`: about 7.5% of lost fumbles in
2025 were the defense's or a returner's.

Writes nothing, generates no picks. Fetch play-by-play as described in
`epa_experiment`'s docstring, then:

    python -m backend.scripts.turnover_experiment --db <snapshot> --pbp-dir pbp
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from backend.database import get_engine, get_session
from backend.scripts.epa_experiment import (THRESHOLDS, Outcome, ats_record,
                                            closing_lines,
                                            incremental_information,
                                            line_margin, rmse)
from backend.scripts.import_nflverse_history import ABBR_FIXUPS

logger = logging.getLogger(__name__)

TRAIN_SEASONS = (2022, 2023, 2024)
TEST_SEASONS = (2025, 2026)
FEATURES = ("to_margin", "giveaways")
PBP_COLUMNS = ["game_id", "season", "week", "game_date", "home_team",
               "away_team", "posteam", "interception", "fumble_lost",
               "fumbled_1_team"]


@dataclass(frozen=True)
class TeamGame:
    game_id: str
    season: int
    week: int
    game_date: date
    team: str
    is_home: bool
    giveaways: int
    takeaways: int


def team_games_from_frame(frame) -> list[TeamGame]:
    """Two rows per game: each side's giveaways and takeaways."""
    gives: dict[tuple[str, str], int] = defaultdict(int)
    for row in frame.itertuples(index=False):
        if row.interception == 1 and isinstance(row.posteam, str):
            gives[(row.game_id, row.posteam)] += 1
        if row.fumble_lost == 1 and isinstance(row.fumbled_1_team, str):
            gives[(row.game_id, row.fumbled_1_team)] += 1

    games = frame.drop_duplicates("game_id")
    out = []
    for g in games.itertuples(index=False):
        h, a = g.home_team, g.away_team
        gh, ga = gives[(g.game_id, h)], gives[(g.game_id, a)]
        d = date.fromisoformat(str(g.game_date))
        out.append(TeamGame(g.game_id, int(g.season), int(g.week), d, h, True, gh, ga))
        out.append(TeamGame(g.game_id, int(g.season), int(g.week), d, a, False, ga, gh))
    return out


def walk_forward(team_games: list[TeamGame]) -> dict[tuple[str, str], dict[str, float]]:
    """(game_id, team) -> each feature from that team's EARLIER games this season.

    Games are ordered by date; a game never sees itself or anything later.
    """
    by_team: dict[tuple[int, str], list[TeamGame]] = defaultdict(list)
    for tg in team_games:
        by_team[(tg.season, tg.team)].append(tg)

    out = {}
    for games in by_team.values():
        games.sort(key=lambda t: t.game_date)
        give = take = 0
        for n, tg in enumerate(games):
            out[(tg.game_id, tg.team)] = (
                {"to_margin": (take - give) / n, "giveaways": give / n} if n
                else {"to_margin": 0.0, "giveaways": 0.0})
            give += tg.giveaways
            take += tg.takeaways
    return out


def feature_diff(name: str, home: dict, away: dict) -> float:
    """Home minus away, signed so positive favours the home side."""
    diff = home[name] - away[name]
    return -diff if name == "giveaways" else diff


def stickiness(team_games: list[TeamGame], name: str) -> tuple[float, int]:
    """Correlation of weeks 1-9 with weeks 10-18, over team-seasons."""
    halves: dict[tuple[int, str], list[list[float]]] = defaultdict(lambda: [[], []])
    for tg in team_games:
        if not 1 <= tg.week <= 18:
            continue
        v = (tg.takeaways - tg.giveaways) if name == "to_margin" else -tg.giveaways
        halves[(tg.season, tg.team)][0 if tg.week <= 9 else 1].append(v)
    pairs = [(sum(a) / len(a), sum(b) / len(b))
             for a, b in halves.values() if len(a) >= 4 and len(b) >= 4]
    import numpy as np
    x, y = np.array(pairs).T
    return float(np.corrcoef(x, y)[0, 1]), len(pairs)


def joined_rows(team_games, lines):
    """(season, {feature: diff}, line_pred, margin) per game with a closing line."""
    feats = walk_forward(team_games)
    sides: dict[str, dict] = defaultdict(dict)
    for tg in team_games:
        sides[tg.game_id]["home" if tg.is_home else "away"] = tg

    rows, unmatched = [], 0
    for gid, s in sides.items():
        h, a = s["home"], s["away"]
        key = (h.game_date, ABBR_FIXUPS.get(h.team, h.team), ABBR_FIXUPS.get(a.team, a.team))
        if key not in lines:
            unmatched += 1
            continue
        spread_home, margin = lines[key]
        diffs = {f: feature_diff(f, feats[(gid, h.team)], feats[(gid, a.team)])
                 for f in FEATURES}
        rows.append((h.season, diffs, line_margin(spread_home), margin))
    return rows, unmatched


def fit_scale(rows, name: str) -> float:
    """Least-squares k in `margin - line = k * diff` (no intercept: the line
    already carries home field)."""
    num = sum(d[name] * (m - lp) for _, d, lp, m in rows)
    den = sum(d[name] ** 2 for _, d, _, _ in rows)
    return num / den if den else 0.0


def report(team_games, lines) -> str:
    rows, unmatched = joined_rows(team_games, lines)
    train = [r for r in rows if r[0] in TRAIN_SEASONS]
    test = [r for r in rows if r[0] in TEST_SEASONS]
    out = ["=" * 72, "TURNOVER EXPERIMENT", "=" * 72,
           f"  joined games {len(rows)} (unmatched {unmatched}); "
           f"train {TRAIN_SEASONS} {len(train)}, test {TEST_SEASONS} {len(test)}", ""]
    for name in FEATURES:
        r_half, n_half = stickiness(team_games, name)
        inc = incremental_information(
            [Outcome(line_pred=lp, model_pred=d[name], margin=m) for _, d, lp, m in rows])
        k = fit_scale(train, name)
        test_rows = [Outcome(line_pred=lp, model_pred=lp + k * d[name], margin=m, season=s)
                     for s, d, lp, m in test]
        out += ["-" * 72, f"{name}", "-" * 72,
                f"  S. weeks 1-9 vs 10-18 correlation {r_half:+.3f} "
                f"over {n_half} team-seasons",
                f"  B. margin ~ line + {name}: coef {inc.model_coef:+.3f} per unit, "
                f"p = {inc.model_p:.4f}  (line {inc.line_coef:+.3f}, n = {inc.n}, "
                f"corr with line {inc.corr:+.3f})",
                f"  C. scale fitted on train: {k:+.3f} points per unit; on test:",
                f"     RMSE line {rmse([(r.line_pred, r.margin) for r in test_rows]):.3f}"
                f" vs line+{name} {rmse([(r.model_pred, r.margin) for r in test_rows]):.3f}",
                f"     {'thresh':>6} {'bet':>5} {'W':>4} {'L':>4} {'win%':>7} {'units':>7} {'p':>7}"]
        for t in THRESHOLDS:
            rec = ats_record(test_rows, threshold=t)
            wr = "    n/a" if rec.win_rate is None else f"{rec.win_rate:7.4f}"
            out.append(f"     {t:>6.1f} {rec.bet:>5} {rec.won:>4} {rec.lost:>4} {wr} "
                       f"{rec.units:>7.2f} {rec.p_value_vs_breakeven:>7.4f}")
        out.append("")
    out += ["  Standard errors assume independent games; each team plays ~17 a",
            "  season, so the effective n is smaller and p-values are optimistic."]
    return "\n".join(out)


def load_frame(pbp_dir: str):
    import pandas as pd
    paths = sorted(glob.glob(os.path.join(pbp_dir, "play_by_play_*.csv.gz")))
    if not paths:
        raise SystemExit(f"no play_by_play_*.csv.gz under {pbp_dir!r}")
    return pd.concat([pd.read_csv(p, usecols=PBP_COLUMNS, low_memory=False) for p in paths])


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Database snapshot. Read only.")
    ap.add_argument("--pbp-dir", required=True)
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db!r} does not exist.")
    session = get_session(get_engine(args.db))
    try:
        lines = closing_lines(session)
    finally:
        session.close()
    team_games = team_games_from_frame(load_frame(args.pbp_dir))
    logger.info("team-games %d, closing lines %d", len(team_games), len(lines))
    print(report(team_games, lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
