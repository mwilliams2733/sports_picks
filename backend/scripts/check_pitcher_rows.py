"""Did this morning's pitcher rows land, and is any of them a phantom 0.5?

Runs from Windows Task Scheduler after the morning scout and its retries.
Read-only with respect to `scheduler.log` and the database: it inspects both
and writes only to `pitcher_health.log`.

Why this check exists
----------------------
Three changes landed on 2026-09-23 whose effect is only visible the next
morning: the scout fetches pitcher scores (plan 019), persists them as
`team_stats` rows under `pitcher_skill_score` (plan 021), and records `None`
rather than a phantom 0.5 for an unannounced starter (plan 022). The
scheduler already logs the evidence -- `MLB pitcher scores: %d game(s), %d
stat row(s) recorded` -- but a log line nobody reads is not a check.

The specific regression worth catching is **a row stored at exactly 0.5**.
Plan 022 made that impossible for a real pitcher: landing on exactly 0.5
requires an ERA of exactly 4.00 AND a K/9 of exactly 8.5. So any row at 0.5
means the producer has started substituting a neutral value again -- silently
pricing announced starters against phantoms, and poisoning the very
measurement plan 021 exists to enable. Before 022 there were four such rows;
after it, zero. This check is what notices if that regresses.

Five outcomes
--------------
**NO_MLB_TODAY** -- no MLB games scheduled. Nothing to check. Healthy.

**ROWS_WRITTEN** -- at least one `pitcher_skill_score` row exists for today's
MLB games, and none of them is exactly 0.5. Healthy.

**NO_STARTERS_ANNOUNCED** -- MLB games exist, the scout's log line for today
is present, and it reports zero stat rows recorded. A working system that
honestly found nothing to price yet. Not an alarm.

**SCOUT_NEVER_RAN** -- MLB games exist and there is no `MLB pitcher scores`
line for today at all. The scheduler was down, or the fetch died before
logging. An alarm.

**AMBIGUOUS_HALVES** -- any stored row for today is exactly 0.5. This
outranks every other outcome: if rows exist and one of them is 0.5, that is
what gets reported, whatever else is true. An alarm.

    python -m backend.scripts.check_pitcher_rows
    python -m backend.scripts.check_pitcher_rows --date 2026-09-23 --json

Exit code 0 when healthy, 1 otherwise, so Task Scheduler's LastTaskResult
carries the answer without anyone opening a log.
"""
from __future__ import annotations

import argparse
import datetime
import enum
import json
import os
import re
from dataclasses import dataclass

from backend.scripts.check_digest import append_entry, rotate  # noqa: F401  (rotate used indirectly via append_entry)

#: Repo root, so the script works whatever directory the task runs from.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_LOG = os.path.join(REPO, "scheduler.log")

#: This check's OWN health log -- deliberately separate from digest_health.log
#: so a pitcher-fetch problem never scrolls a digest problem out of view.
HEALTH_LOG = os.path.join(REPO, "pitcher_health.log")

#: Same budget as the digest check's health log.
MAX_BYTES = 128 * 1024
KEEP = 3

_PITCHER_RE = re.compile(
    r"MLB pitcher scores: (\d+) game\(s\), (\d+) stat row\(s\) recorded")

#: A stored score at exactly this value is not a real measurement -- see the
#: module docstring for why plan 022 makes it impossible for a real pitcher.
AMBIGUOUS_VALUE = 0.5


class Outcome(enum.Enum):
    NO_MLB_TODAY = "no-mlb-today"
    ROWS_WRITTEN = "rows-written"
    NO_STARTERS_ANNOUNCED = "no-starters-announced"
    SCOUT_NEVER_RAN = "scout-never-ran"
    AMBIGUOUS_HALVES = "ambiguous-halves"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    ok: bool
    rows_now: int
    detail: str

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def pitcher_lines(lines, target_date) -> list[str]:
    """`scheduler.log` lines reporting the pitcher fetch for `target_date`.

    `scheduler.log` is not rotated daily, so a line has to be matched to its
    day the same way `check_digest.digest_lines` does it -- otherwise
    yesterday's run reads as today's and the check reports healthy forever.
    The pitcher-fetch line carries no date of its own (unlike the digest's
    "empty" line), so -- exactly like the "sent" line -- its leading
    timestamp is what dates it.
    """
    stamp = target_date.isoformat()
    out = []
    for line in lines:
        if _PITCHER_RE.search(line) and line.lstrip().startswith(stamp):
            out.append(line)
    return out


def classify(lines, target_date, *, mlb_games_today: int,
            rows_today: list[float]) -> Result:
    """Decide which of the five outcomes today is in.

    `AMBIGUOUS_HALVES` is checked first and outranks everything else: if
    rows exist and any is exactly 0.5, that is reported whatever the log
    line or the rest of the rows say.
    """
    if any(v == AMBIGUOUS_VALUE for v in rows_today):
        return Result(
            Outcome.AMBIGUOUS_HALVES, False, len(rows_today),
            f"{sum(1 for v in rows_today if v == AMBIGUOUS_VALUE)} of "
            f"{len(rows_today)} pitcher_skill_score row(s) for today are "
            f"exactly {AMBIGUOUS_VALUE}: plan 022 made a real pitcher landing "
            f"there impossible (ERA exactly 4.00 AND K/9 exactly 8.5), so the "
            f"producer has started substituting a neutral value again")

    if mlb_games_today == 0:
        return Result(Outcome.NO_MLB_TODAY, True, len(rows_today),
                      "no MLB games scheduled today -- nothing to check")

    if rows_today:
        return Result(Outcome.ROWS_WRITTEN, True, len(rows_today),
                      f"{len(rows_today)} pitcher_skill_score row(s) recorded "
                      f"for today's MLB games, none at {AMBIGUOUS_VALUE}")

    relevant = pitcher_lines(lines, target_date)
    if not relevant:
        return Result(
            Outcome.SCOUT_NEVER_RAN, False, 0,
            "MLB games are scheduled today but no 'MLB pitcher scores' line "
            "exists for today at all: the scheduler was down, or the fetch "
            "died before logging")

    m = _PITCHER_RE.search(relevant[-1])
    stat_rows = int(m.group(2)) if m else 0
    if stat_rows == 0:
        return Result(
            Outcome.NO_STARTERS_ANNOUNCED, True, 0,
            "the scout ran and honestly reported 0 stat row(s) recorded -- "
            "no starters were announced yet, not a fault")

    # The log line claims rows were written but none were found for today --
    # treat this the same as never having run, since the measurable evidence
    # (the rows themselves) disagrees with the log.
    return Result(
        Outcome.SCOUT_NEVER_RAN, False, 0,
        f"the scout's log line claims {stat_rows} stat row(s) recorded but "
        f"none exist for today's MLB games now")


def mlb_games_today(db_path: str, target_date) -> int:
    """How many MLB games are on the card for `target_date`."""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "select count(*) from games where sport = 'mlb' and date = ?",
            (target_date.isoformat(),)).fetchone()[0]
    finally:
        con.close()


def pitcher_rows_for(db_path: str, target_date) -> list[float]:
    """Every `pitcher_skill_score` value recorded for `target_date`'s MLB games."""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select ts.value from team_stats ts "
            "join games g on g.id = ts.game_id "
            "where g.sport = 'mlb' and g.date = ? and ts.stat_type = 'pitcher_skill_score'",
            (target_date.isoformat(),)).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--db", default=os.path.join(REPO, "sports_picks.db"))
    ap.add_argument("--date", help="ISO date. Defaults to today in ET.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--health-log", default=HEALTH_LOG,
                    help="Where to append the verdict. '' disables.")
    args = ap.parse_args(argv)

    if args.date:
        target = datetime.date.fromisoformat(args.date)
    else:
        from backend.time_utils import et_today
        target = et_today()

    def _emit(text: str, code: int) -> int:
        print(text)
        if args.health_log:
            try:
                append_entry(args.health_log, text, exit_code=code,
                             max_bytes=MAX_BYTES, keep=KEEP)
            except OSError as e:
                # Never let the record-keeping fail the check itself.
                print(f"(could not write {args.health_log}: {type(e).__name__})")
        return code

    try:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return _emit(f"CANNOT READ LOG {args.log}: {type(e).__name__}", 1)

    result = classify(lines, target,
                      mlb_games_today=mlb_games_today(args.db, target),
                      rows_today=pitcher_rows_for(args.db, target))

    if args.json:
        report = json.dumps({"date": target.isoformat(),
                             "outcome": result.outcome.value, "ok": result.ok,
                             "rows_now": result.rows_now,
                             "detail": result.detail}, indent=2)
    else:
        report = "\n".join([
            f"[{target}] {'OK' if result.ok else 'PROBLEM'}: "
            f"{result.outcome.value}",
            f"  pitcher_skill_score rows for today: {result.rows_now}",
            f"  {result.detail}"])
    return _emit(report, result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
