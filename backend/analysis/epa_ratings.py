"""Walk-forward EPA/play team ratings for NFL, and their map to a margin.

Why EPA and not point differential
----------------------------------
Every football feature this repo fits on is derived from the final score:
point differential, win-loss splits, Elo. A score is the outcome of roughly
130 scrimmage plays plus special teams plus turnover luck, so it is a very
noisy estimate of how well a team actually played. Expected Points Added
measures each snap against the league's historical scoring value for that
down, distance and field position, which is why EPA/play stabilises in
around a quarter of the games a point differential needs.

nflverse publishes EPA per play for every snap since 1999. This module turns
those snaps into a pre-game rating and then into a predicted margin.

The three decisions
-------------------
**Scrimmage plays only.** Punts, kickoffs and field goals carry EPA but
measure special teams, not the offence the rating claims to describe.

**Garbage time is dropped.** Snaps taken outside a 5%-95% win probability
band are excluded by default: a team leading by 31 in the fourth is playing
the clock, and those plays measure score state rather than strength. The
filter is a judgement call, so `drop_garbage_time` exposes both choices and
the experiment reports each.

**A rating is built from earlier games only.** `walk_forward_ratings` emits
each team's rating *as it stood before* the game, never including that
game's own snaps. This is the whole experiment: a rating that peeks at its
own game scores every prediction against a partial answer key and produces a
beautiful, false result. `test_epa_ratings.py` pins it from both sides --
changing a game must not move that game's rating, and must move the next
one's.

Weighting
---------
Snaps are weighted by an exponential decay with a ten-game half life, so a
team that changed in November is not averaged with the team it was in
September, and by a `PRIOR_PLAYS` prior of league-average snaps in the
denominator, so a one-game history is regressed hard toward average rather
than declaring a Week 2 team elite. Across the offseason the accumulated
weight is multiplied by `SEASON_CARRYOVER`: rosters and coaches turn over,
and carrying a rating through March undiluted treats it as another bye week.

The scale from EPA to points is FITTED, never assumed -- see `fit_margin`.
A hand-tuned "about 62 plays a game" constant is indistinguishable from one
that quietly saw the test set.
"""
from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass

#: Play types that count. Special teams are a different skill.
SCRIMMAGE_PLAYS = ("pass", "rush")

#: Snaps outside this win-probability band are garbage time.
GARBAGE_TIME_WP = 0.05

#: Half life of the exponential decay, in games.
HALF_LIFE_GAMES = 10.0

#: League-average snaps added to the denominator, regressing a short history
#: toward average. Roughly four games of offence.
PRIOR_PLAYS = 250.0

#: Fraction of accumulated weight kept across an offseason.
SEASON_CARRYOVER = 0.6


@dataclass(frozen=True)
class Play:
    """One row of an nflverse play-by-play export."""
    game_id: str
    season: int
    week: int | None
    game_date: datetime.date
    home_team: str
    away_team: str
    posteam: str | None
    defteam: str | None
    play_type: str | None
    epa: float | None
    wp: float | None
    aborted_play: int | None


@dataclass(frozen=True)
class TeamGame:
    """One team's side of one game, aggregated."""
    game_id: str
    season: int
    game_date: datetime.date
    team: str
    opponent: str
    is_home: bool
    off_epa: float
    off_plays: int
    def_epa: float
    def_plays: int


@dataclass(frozen=True)
class Rating:
    """A team's strength as it stood BEFORE some game.

    `off` and `deff` are both EPA allowed-or-gained per play, on the same
    scale and the same sign: a good defence has a LOW `deff`. The subtraction
    happens in `predict_margin`, so neither number is pre-flipped.

    `off_plays` / `def_plays` are the decayed snap counts behind the estimate,
    kept so a caller can tell a confident rating from a Week 1 placeholder.
    """
    off: float
    deff: float
    off_plays: float
    def_plays: float

    @property
    def net(self) -> float:
        """Points-per-play strength: what the offence gains less what the
        defence concedes."""
        return self.off - self.deff


@dataclass(frozen=True)
class MarginFit:
    """The fitted map from an EPA edge to a point margin."""
    home_field: float
    points_per_epa: float


def usable_plays(plays, *, drop_garbage_time: bool = True) -> list[Play]:
    """Keep the snaps a rating should be built from.

    A null `epa` is nflverse declining to model the play; treating it as 0.0
    would quietly pull every rating toward average. A null `wp` is unknown,
    not lopsided, so it survives the garbage-time filter -- absent is never
    the same as defaulted.
    """
    kept = []
    for p in plays:
        if p.play_type not in SCRIMMAGE_PLAYS:
            continue
        if p.epa is None or p.posteam is None or p.defteam is None:
            continue
        if p.aborted_play:
            continue
        if (drop_garbage_time and p.wp is not None
                and not (GARBAGE_TIME_WP <= p.wp <= 1.0 - GARBAGE_TIME_WP)):
            continue
        kept.append(p)
    return kept


def aggregate_team_games(plays, *, drop_garbage_time: bool = True
                         ) -> list[TeamGame]:
    """Reduce snaps to two rows per game, one per team.

    Both teams are always emitted, even one that never ran a usable snap --
    dropping that row would silently delete the game from its opponent's
    schedule too.
    """
    meta: dict[str, Play] = {}
    off_sum: dict[tuple[str, str], float] = collections.defaultdict(float)
    off_n: dict[tuple[str, str], int] = collections.defaultdict(int)
    def_sum: dict[tuple[str, str], float] = collections.defaultdict(float)
    def_n: dict[tuple[str, str], int] = collections.defaultdict(int)

    for p in usable_plays(plays, drop_garbage_time=drop_garbage_time):
        meta.setdefault(p.game_id, p)
        off_sum[(p.game_id, p.posteam)] += p.epa
        off_n[(p.game_id, p.posteam)] += 1
        def_sum[(p.game_id, p.defteam)] += p.epa
        def_n[(p.game_id, p.defteam)] += 1

    rows: list[TeamGame] = []
    for game_id, ref in meta.items():
        for team, opponent in ((ref.home_team, ref.away_team),
                               (ref.away_team, ref.home_team)):
            o_n, d_n = off_n[(game_id, team)], def_n[(game_id, team)]
            rows.append(TeamGame(
                game_id=game_id, season=ref.season, game_date=ref.game_date,
                team=team, opponent=opponent, is_home=team == ref.home_team,
                off_epa=off_sum[(game_id, team)] / o_n if o_n else 0.0,
                off_plays=o_n,
                def_epa=def_sum[(game_id, team)] / d_n if d_n else 0.0,
                def_plays=d_n))
    return rows


class _Accumulator:
    """One team's decayed running totals."""

    def __init__(self) -> None:
        self.off_num = self.off_den = 0.0
        self.def_num = self.def_den = 0.0
        self.season: int | None = None

    def rating(self) -> Rating:
        # A team with no history and no prior has no estimate at all; league
        # average is the only honest answer. Reachable only when PRIOR_PLAYS
        # is 0, which the lookahead tests do deliberately.
        def _mean(num: float, den: float) -> float:
            total = den + PRIOR_PLAYS
            return num / total if total else 0.0

        return Rating(
            off=_mean(self.off_num, self.off_den),
            deff=_mean(self.def_num, self.def_den),
            off_plays=self.off_den, def_plays=self.def_den)

    def start_season(self, season: int) -> None:
        if self.season is not None and season != self.season:
            for attr in ("off_num", "off_den", "def_num", "def_den"):
                setattr(self, attr, getattr(self, attr) * SEASON_CARRYOVER)
        self.season = season

    def absorb(self, tg: TeamGame) -> None:
        decay = 0.5 ** (1.0 / HALF_LIFE_GAMES)
        self.off_num = self.off_num * decay + tg.off_epa * tg.off_plays
        self.off_den = self.off_den * decay + tg.off_plays
        self.def_num = self.def_num * decay + tg.def_epa * tg.def_plays
        self.def_den = self.def_den * decay + tg.def_plays


def walk_forward_ratings(team_games) -> dict[tuple[str, str], Rating]:
    """Map (game_id, team) -> that team's rating BEFORE that game.

    Games are processed in date order and each team's rating is emitted
    before its own snaps are absorbed, so no rating can contain the result it
    is used to predict.
    """
    state: dict[str, _Accumulator] = collections.defaultdict(_Accumulator)
    out: dict[tuple[str, str], Rating] = {}

    for tg in sorted(team_games, key=lambda g: (g.game_date, g.game_id)):
        acc = state[tg.team]
        acc.start_season(tg.season)
        out[(tg.game_id, tg.team)] = acc.rating()   # BEFORE absorbing
        acc.absorb(tg)
    return out


def epa_edge(home: Rating, away: Rating) -> float:
    """The home team's net EPA/play advantage."""
    return home.net - away.net


def margin_from_edge(edge: float, fit: MarginFit) -> float:
    """Convert an EPA edge straight to points.

    The single definition of the EPA-to-margin map. `predict_margin` is a
    convenience wrapper over it, and the experiment calls this directly
    because it has already reduced each game to an edge -- two hand-written
    copies of one formula is how they drift apart.
    """
    return fit.home_field + fit.points_per_epa * edge


def predict_margin(home: Rating, away: Rating, fit: MarginFit) -> float:
    """Predicted `home_score - away_score`.

    Positive means the home side. That matches `Odds.spread_home` negated:
    a home favourite is quoted at spread_home -3.5 and predicted at +3.5.
    """
    return margin_from_edge(epa_edge(home, away), fit)


def fit_margin(rows) -> MarginFit:
    """Least-squares fit of margin on EPA edge over `(edge, margin)` pairs.

    The intercept is home-field advantage in points and the slope converts an
    EPA/play edge into points. Both are fitted rather than assumed, and the
    experiment fits them on training seasons only.
    """
    rows = list(rows)
    if len(rows) < 2:
        raise ValueError(f"a margin fit needs at least 2 games, got {len(rows)}")

    import numpy as np
    edges = np.array([r[0] for r in rows], dtype=float)
    margins = np.array([r[1] for r in rows], dtype=float)
    slope, intercept = np.polyfit(edges, margins, 1)
    return MarginFit(home_field=float(intercept), points_per_epa=float(slope))
