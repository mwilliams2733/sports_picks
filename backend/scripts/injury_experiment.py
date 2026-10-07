"""Do NFL injuries tell us anything the closing line does not?

Why this exists
---------------
The owner asked (2026-10-06) to start the criteria backlog
(`docs/criteria-backlog.md`) with injuries. Nothing in the model knows who is
hurt. Team-level stats are already in the close (`epa_experiment`,
`turnover_experiment`), so the question is narrower: is the market's
adjustment for a missing player complete?

Definitions, fixed before the first run:

* **Leader.** A team's season-to-date leader in pass attempts (QB), targets
  (receiver) or carries (rusher), counted over that team's games strictly
  before this one. No leader in a team's first game of a season.
* **Out.** The leader is listed `Out` or `Doubtful` on that week's official
  injury report (nflverse `injuries`). Known before kickoff, so bettable.
  `Questionable` is reported separately and never counted as out.
* **Hindsight** (sides only, NOT bettable): the QB leader threw fewer than
  half the team's passes in this game. It catches benchings, IR stints that
  never reach the weekly report and in-game injuries. It is an upper bound on
  what perfect knowledge would have been worth, not a strategy.

Tests
-----
**A. Sides and totals.** Over regular-season games 2022-2026:

    margin = a + b*spread_line + c*(away_qb_out - home_qb_out)
    total  = a + b*total_line  + c*(home_qb_out + away_qb_out)

`c` is the residual: points the market left on the table. Plus the ATS record
of fading (and of backing) a team whose starting QB is out, against -110
break-even. The real sample is the number of QB-out games, printed with each
result -- not the number of games.

**B. Props (projection).** Per player-game with a walk-forward baseline (the
player's mean so far this season, MIN_PRIOR games, over the market floor):

    actual = a + b*base + c1*base*teammate_leader_out + c2*base*qb_out

`c1 = +0.15` means the player gains 15% of his baseline when the team's
target (or carry) leader is out. Standard errors are clustered by game:
teammates share a game script, so player-games are not independent.

The line test is not run: this season's prop lines (since 2026-09-20) cover
too few injury cases to read, and pbp names do not match book names. A
positive B says "better projections", not "edge" -- the same verdict as
`prop_matchup_experiment`.

Writes nothing, generates no picks, needs no database. Fetch play-by-play as
in `epa_experiment`'s docstring, and the injury reports:

    for y in 2022 2023 2024 2025 2026; do
      curl -sL -o inj/injuries_$y.csv \\
        https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_$y.csv
    done

    python -m backend.scripts.injury_experiment --pbp-dir pbp --injury-dir inj
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from backend.scripts.epa_experiment import BREAK_EVEN

logger = logging.getLogger(__name__)

OUT_STATUSES = frozenset({"Out", "Doubtful"})
MIN_PRIOR = 3
#: market -> (stat, leader kind whose absence is the treatment, baseline floor)
#: Floors match prop_matchup_experiment.MARKETS: roughly who books post for.
PROP_MARKETS = {
    "player_reception_yds": ("rec_yards", "target", 20.0),
    "player_rush_yds": ("rush_yards", "carry", 25.0),
}
PBP_COLUMNS = ["game_id", "season", "season_type", "week", "home_team",
               "away_team", "posteam", "pass_attempt", "passer_player_id",
               "receiver_player_id", "receiving_yards", "rusher_player_id",
               "rushing_yards", "passing_yards", "result", "total", "spread_line",
               "total_line"]
ROSTER_COLUMNS = ["season", "week", "team", "gsis_id", "status", "game_type"]


@dataclass
class TeamGame:
    """One team's box in one game, as counted from play-by-play."""
    game_id: str
    season: int
    week: int
    team: str
    attempts: Counter = field(default_factory=Counter)   # passer -> attempts
    pass_yards: Counter = field(default_factory=Counter)
    targets: Counter = field(default_factory=Counter)    # receiver -> targets
    carries: Counter = field(default_factory=Counter)    # rusher -> carries
    rec_yards: Counter = field(default_factory=Counter)
    rush_yards: Counter = field(default_factory=Counter)

    def players(self) -> set[str]:
        return set(self.targets) | set(self.carries)


@dataclass(frozen=True)
class GameLine:
    game_id: str
    season: int
    week: int
    home: str
    away: str
    margin: float
    total: float
    spread_line: float   # nflverse: positive = home favoured
    total_line: float


#: What a "leader" leads in. `touches` is the experiment's definition;
#: `yards` is what production can compute, since `player_stats` stores
#: yards but not attempts, targets or carries.
LEADER_BASIS = {
    "touches": {"qb": "attempts", "target": "targets", "carry": "carries"},
    "yards": {"qb": "pass_yards", "target": "rec_yards", "carry": "rush_yards"},
}


def walk_forward_leaders(team_games: list[TeamGame],
                         basis: str = "touches") -> dict[tuple[str, str], dict[str, str]]:
    """(game_id, team) -> {"qb"|"target"|"carry": player id} from PRIOR games only.

    A team's games are taken in week order within a season, and this game's
    own counts are added only after its leaders are read, so a player cannot
    become the leader on the strength of the game being predicted.
    """
    by_team = defaultdict(list)
    for tg in team_games:
        by_team[(tg.season, tg.team)].append(tg)
    out = {}
    for games in by_team.values():
        games.sort(key=lambda g: g.week)
        sums = {"qb": Counter(), "target": Counter(), "carry": Counter()}
        for g in games:
            out[(g.game_id, g.team)] = {k: c.most_common(1)[0][0]
                                        for k, c in sums.items() if c}
            for kind, stat in LEADER_BASIS[basis].items():
                sums[kind].update(getattr(g, stat))
    return out


class Availability:
    """Who was known to be missing before kickoff, from two public sources.

    * The injury report: `Out` / `Doubtful`. Filtered on `game_type`, which
      every season's file has -- 2022-2024 have no `season_type` column, and
      filtering on it silently dropped three seasons (first run, 2026-10-06).
    * The weekly roster: a player not `ACT` that week (IR, gameday inactive,
      released) or no longer on the team's roster. A player on injured reserve
      drops off the weekly injury report, so the report alone sees only the
      first game of a long absence.

    Gameday inactives are announced about 90 minutes before kickoff, before
    the close. A week with no roster rows for the team is treated as unknown
    (only the report counts), never as everyone missing.
    """

    def __init__(self, injuries, rosters=None):
        inj = injuries[(injuries.game_type == "REG") & injuries.report_status.notna()]
        self.report = {(int(r.season), int(r.week), r.team, r.gsis_id): r.report_status
                       for r in inj.itertuples(index=False)}
        self.roster: dict[tuple[int, int, str, str], str] = {}
        self.roster_weeks: set[tuple[int, int, str]] = set()
        if rosters is not None:
            ros = rosters[rosters.game_type == "REG"]
            for r in ros.itertuples(index=False):
                key = (int(r.season), int(r.week), r.team)
                self.roster_weeks.add(key)
                if isinstance(r.gsis_id, str):
                    # ACT wins over a duplicate row saying otherwise.
                    if self.roster.get(key + (r.gsis_id,)) != "ACT":
                        self.roster[key + (r.gsis_id,)] = r.status

    def status(self, season: int, week: int, team: str, player: str | None) -> str | None:
        return self.report.get((season, week, team, player)) if player else None

    def absent(self, season: int, week: int, team: str, player: str | None) -> bool:
        if player is None:
            return False
        if self.report.get((season, week, team, player)) in OUT_STATUSES:
            return True
        if (season, week, team) not in self.roster_weeks:
            return False
        return self.roster.get((season, week, team, player)) != "ACT"


def team_games_from_frame(frame) -> tuple[list[TeamGame], list[GameLine]]:
    df = frame[frame.season_type == "REG"]
    tgs: dict[tuple[str, str], TeamGame] = {}
    for r in df.itertuples(index=False):
        if not isinstance(r.posteam, str):
            continue
        key = (r.game_id, r.posteam)
        tg = tgs.get(key)
        if tg is None:
            tg = tgs[key] = TeamGame(r.game_id, int(r.season), int(r.week), r.posteam)
        if r.pass_attempt == 1 and isinstance(r.passer_player_id, str):
            tg.attempts[r.passer_player_id] += 1
            if not math.isnan(r.passing_yards):
                tg.pass_yards[r.passer_player_id] += r.passing_yards
        if r.pass_attempt == 1 and isinstance(r.receiver_player_id, str):
            tg.targets[r.receiver_player_id] += 1
            if not math.isnan(r.receiving_yards):
                tg.rec_yards[r.receiver_player_id] += r.receiving_yards
        if isinstance(r.rusher_player_id, str):
            tg.carries[r.rusher_player_id] += 1
            if not math.isnan(r.rushing_yards):
                tg.rush_yards[r.rusher_player_id] += r.rushing_yards
    games = (df.dropna(subset=["result", "spread_line", "total_line"])
             .drop_duplicates("game_id"))
    lines = [GameLine(r.game_id, int(r.season), int(r.week), r.home_team,
                      r.away_team, float(r.result), float(r.total),
                      float(r.spread_line), float(r.total_line))
             for r in games.itertuples(index=False)]
    return list(tgs.values()), lines


# --- A. sides and totals ----------------------------------------------------

#: Variant -> label. Each SideRow carries (home, away) flags for every one.
VARIANTS = {
    "absent": "starter absent (report Out/Doubtful, or roster not active)",
    "first": "  ...first game of the absence only",
    "report": "  ...injury report Out/Doubtful only",
    "questionable": "starter Questionable and not absent",
    "hindsight": "HINDSIGHT (not bettable): starter threw < half the passes",
}


@dataclass(frozen=True)
class SideRow:
    line: GameLine
    flags: dict   # variant -> (home flag, away flag)


def side_rows(lines, team_games, leaders, avail: Availability) -> list[SideRow]:
    tg_by = {(tg.game_id, tg.team): tg for tg in team_games}
    weeks = defaultdict(list)
    for tg in team_games:
        weeks[(tg.season, tg.team)].append(tg.week)

    def previous_week(season: int, team: str, week: int) -> int | None:
        earlier = [w for w in weeks[(season, team)] if w < week]
        return max(earlier) if earlier else None

    rows = []
    for gl in lines:
        # Week 1 has no leaders yet, so it cannot say whether a starter is out.
        if not (leaders.get((gl.game_id, gl.home)) and leaders.get((gl.game_id, gl.away))):
            continue
        per_side = []
        for team in (gl.home, gl.away):
            qb = leaders[(gl.game_id, team)].get("qb")
            absent = avail.absent(gl.season, gl.week, team, qb)
            pw = previous_week(gl.season, team, gl.week)
            was_absent = pw is not None and avail.absent(gl.season, pw, team, qb)
            st = avail.status(gl.season, gl.week, team, qb)
            tg = tg_by.get((gl.game_id, team))
            thrown = tg.attempts.get(qb, 0) if (tg and qb) else 0
            total = sum(tg.attempts.values()) if tg else 0
            per_side.append({
                "absent": absent,
                "first": absent and not was_absent,
                "report": st in OUT_STATUSES,
                "questionable": st == "Questionable" and not absent,
                "hindsight": qb is not None and total > 0 and thrown < total / 2,
            })
        rows.append(SideRow(gl, {v: (per_side[0][v], per_side[1][v]) for v in VARIANTS}))
    return rows


def ats(rows, variant: str, fade: bool) -> tuple[int, int, int]:
    """Bet against (fade=True) or on the one team whose starter is out."""
    w = l = p = 0
    for r in rows:
        home_out, away_out = r.flags[variant]
        if home_out == away_out:
            continue
        cover = r.line.margin - r.line.spread_line   # >0: home covered
        if cover == 0:
            p += 1
            continue
        # Fading bets the healthy side, which is home exactly when away is out.
        home_bet = away_out if fade else home_out
        won = (cover > 0) == home_bet
        w, l = w + won, l + (not won)
    return w, l, p


def binom_p(won: int, decided: int) -> float:
    from scipy import stats
    return float(stats.binom.sf(won - 1, decided, BREAK_EVEN)) if decided else 1.0


def _coef_line(name: str, beta, se, p, i: int, unit: str) -> str:
    return (f"    {name}: c = {beta[i]:+.2f} {unit} (95% CI {beta[i] - 1.96 * se[i]:+.2f}"
            f"..{beta[i] + 1.96 * se[i]:+.2f}), p = {p[i]:.2f}")


def report_sides(rows) -> list[str]:
    out = ["-" * 72, "A. SIDES AND TOTALS  (regular season, nflverse close)", "-" * 72,
           f"  {len(rows)} games (week 1 excluded: no leader yet).",
           "  c = points the close left over per missing starter; 0 = fully priced.",
           "  'n treated' is the real sample, not the game count."]
    ids = [r.line.game_id for r in rows]
    for variant, label in VARIANTS.items():
        home = [float(r.flags[variant][0]) for r in rows]
        away = [float(r.flags[variant][1]) for r in rows]
        n_t = int(sum(home) + sum(away))
        bs, ss, ps, _ = clustered_ols(
            [[r.line.spread_line for r in rows], [a - h for h, a in zip(home, away)]],
            [r.line.margin for r in rows], ids)
        bt, st, pt, _ = clustered_ols(
            [[r.line.total_line for r in rows], [h + a for h, a in zip(home, away)]],
            [r.line.total for r in rows], ids)
        out += [f"  {label}  (n treated {n_t})",
                _coef_line("spread", bs, ss, ps, 2, "pts to the healthy side"),
                _coef_line("total ", bt, st, pt, 2, "pts over the close")]
        if variant in ("absent", "first"):
            for fade in (True, False):
                w, l, p = ats(rows, variant, fade)
                d = w + l
                rate = f"{w / d:.3f}" if d else "n/a"
                out.append(f"    ATS {'fading' if fade else 'backing'} the starter-out team: "
                           f"{w}-{l}-{p}  {rate}  {w * (100 / 110) - l:+.1f}u  "
                           f"p vs 52.4% = {binom_p(w, d):.2f}")
    return out


# --- B. props ---------------------------------------------------------------

def prop_rows(team_games, leaders, avail: Availability):
    """(market, actual, baseline, teammate_leader_out, qb_out, game_id) per player-game."""
    by_team = defaultdict(list)
    for tg in team_games:
        by_team[(tg.season, tg.team)].append(tg)
    rows = []
    for games in by_team.values():
        games.sort(key=lambda g: g.week)
        hist: dict[tuple[str, str], list[float]] = defaultdict(list)
        for g in games:
            lead = leaders.get((g.game_id, g.team), {})
            qb_out = avail.absent(g.season, g.week, g.team, lead.get("qb"))
            for market, (stat, kind, floor) in PROP_MARKETS.items():
                leader = lead.get(kind)
                leader_out = avail.absent(g.season, g.week, g.team, leader)
                for pid in g.players():
                    prior = hist[(market, pid)]
                    if (pid != leader and len(prior) >= MIN_PRIOR
                            and sum(prior) / len(prior) >= floor):
                        rows.append((market, float(getattr(g, stat)[pid]),
                                     sum(prior) / len(prior), leader_out, qb_out,
                                     g.game_id))
            for market, (stat, _, _) in PROP_MARKETS.items():
                for pid in g.players():
                    hist[(market, pid)].append(float(getattr(g, stat)[pid]))
    return rows


def clustered_ols(x_cols, y, clusters):
    """OLS with standard errors clustered on `clusters` (CR0 sandwich).

    With one row per cluster this is the heteroskedasticity-robust (HC0)
    estimator, which is what the sides regression gets.
    """
    import numpy as np
    from scipy import stats
    x = np.column_stack([np.ones(len(y))] + [np.asarray(c, float) for c in x_cols])
    y = np.asarray(y, float)
    inv = np.linalg.pinv(x.T @ x)
    beta = inv @ x.T @ y
    resid = y - x @ beta
    groups = defaultdict(list)
    for i, c in enumerate(clusters):
        groups[c].append(i)
    meat = np.zeros((x.shape[1], x.shape[1]))
    for idx in groups.values():
        s = x[idx].T @ resid[idx]
        meat += np.outer(s, s)
    g = len(groups)
    se = np.sqrt(np.diag(inv @ meat @ inv) * g / max(g - 1, 1))
    p = 2 * stats.t.sf(np.abs(beta / se), max(g - 1, 1))
    return beta, se, p, g


def report_props(rows) -> list[str]:
    out = ["-" * 72, "B. PROPS  (projection only -- no line test)", "-" * 72]
    for market, (_, kind, floor) in PROP_MARKETS.items():
        rs = [r for r in rows if r[0] == market]
        base = [r[2] for r in rs]
        beta, se, p, g = clustered_ols(
            [base, [r[2] * r[3] for r in rs], [r[2] * r[4] for r in rs]],
            [r[1] for r in rs], [r[5] for r in rs])
        n_lo = sum(r[3] for r in rs)
        n_qo = sum(r[4] for r in rs)
        out += [f"  {market}: {len(rs)} player-games in {g} games, floor {floor:.0f} yds",
                f"    {kind} leader out (n {n_lo}): c1 = {beta[2]:+.3f} x baseline "
                f"(95% CI {beta[2] - 1.96 * se[2]:+.3f}..{beta[2] + 1.96 * se[2]:+.3f}), p = {p[2]:.2g}",
                f"    starting QB out   (n {n_qo}): c2 = {beta[3]:+.3f} x baseline "
                f"(95% CI {beta[3] - 1.96 * se[3]:+.3f}..{beta[3] + 1.96 * se[3]:+.3f}), p = {p[3]:.2g}",
                f"    baseline slope b = {beta[1]:.3f}"]
    out += ["", "  Standard errors are clustered by game. A player still appears in",
            "  many games, so read intervals as somewhat optimistic."]
    return out


def main(argv=None) -> int:
    import pandas as pd
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbp-dir", required=True)
    ap.add_argument("--injury-dir", required=True)
    ap.add_argument("--roster-dir", required=True)
    ap.add_argument("--leader-basis", choices=sorted(LEADER_BASIS), default="touches")
    args = ap.parse_args(argv)
    pbp = sorted(glob.glob(os.path.join(args.pbp_dir, "play_by_play_*.csv.gz")))
    inj = sorted(glob.glob(os.path.join(args.injury_dir, "injuries_*.csv")))
    ros = sorted(glob.glob(os.path.join(args.roster_dir, "roster_weekly_*.csv")))
    if not (pbp and inj and ros):
        raise SystemExit("missing play_by_play_*.csv.gz, injuries_*.csv or "
                         "roster_weekly_*.csv -- see this module's docstring")
    frame = pd.concat([pd.read_csv(p, usecols=PBP_COLUMNS, low_memory=False) for p in pbp])
    avail = Availability(
        pd.concat([pd.read_csv(p) for p in inj]),
        pd.concat([pd.read_csv(p, usecols=ROSTER_COLUMNS, low_memory=False) for p in ros]))
    team_games, lines = team_games_from_frame(frame)
    leaders = walk_forward_leaders(team_games, args.leader_basis)
    print("=" * 72)
    print("INJURY EXPERIMENT  (NFL regular season 2022-2026)")
    print("=" * 72)
    print("\n".join(report_sides(side_rows(lines, team_games, leaders, avail))))
    print("\n".join(report_props(prop_rows(team_games, leaders, avail))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
