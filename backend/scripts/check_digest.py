"""Did this morning's digest go out, and if not, which way did it fail?

Runs from Windows Task Scheduler after the 11:00 ET send. Read-only: it
inspects `scheduler.log` and the database and writes nothing to either.

Three outcomes, not two
-----------------------
A check that only asks "did it send" produces an answer nobody can act on.
These have different causes and different fixes:

**SENT** -- nothing to do.

**EMPTY, quiet day** -- the digest ran and the selector honestly found
nothing. A working system. Not an alarm.

**EMPTY, but picks exist now** -- the digest ran at 11:00, found nothing,
and picks for that date were written afterwards. That is the regression
`d30016c` fixed: window jobs price each game two hours before it starts, so
an evening card produced its picks around 22:00 UTC while the digest had
already given up at 15:00 UTC. If this fires again the morning slate is
landing too late.

**NEVER RAN** -- no digest line for today at all. The scheduler was down, or
the 8am scout failed before reaching the digest hour.

The `Morning slate for <date>` line is the upstream signal and is reported
separately, because the digest can send off a stale slate while this
morning's scout never ran. Absent is distinguished from "nothing scheduled":
the first is a broken scout, the second is an empty card.

    python -m backend.scripts.check_digest
    python -m backend.scripts.check_digest --date 2026-09-23 --json

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

#: Repo root, so the script works whatever directory the task runs from.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_LOG = os.path.join(REPO, "scheduler.log")

_SENT_RE = re.compile(r"Digest sent to (\d+) recipient")
_EMPTY_RE = re.compile(r"Digest for (\d{4}-\d{2}-\d{2}) is empty")
_SLATE_RE = re.compile(r"Morning slate for (\d{4}-\d{2}-\d{2}): (.+?)\s*$")


#: Hour (ET) the digest job fires. Mirrors config digest.send_hour_et.
DEFAULT_SEND_HOUR_ET = 11


class Outcome(enum.Enum):
    SENT = "sent"
    TOO_EARLY = "too-early"
    EMPTY_QUIET = "empty-quiet-day"
    EMPTY_BUT_PICKS_EXIST = "empty-but-picks-exist"
    NEVER_RAN = "never-ran"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    ok: bool
    slate: str | None
    picks_now: int
    detail: str

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def digest_lines(lines, target_date) -> list[str]:
    """Digest log lines that are about `target_date`.

    `scheduler.log` is not rotated daily, so a line has to be matched to its
    day or yesterday's success reads as today's and the check reports healthy
    every morning forever.

    An "empty" line carries the date it is ABOUT, which is authoritative --
    a run just after midnight UTC would otherwise be filed under the wrong
    day. A "sent" line carries no date, so its leading timestamp is used.
    """
    stamp = target_date.isoformat()
    out = []
    for line in lines:
        empty = _EMPTY_RE.search(line)
        if empty:
            if empty.group(1) == stamp:
                out.append(line)
        elif _SENT_RE.search(line) and line.lstrip().startswith(stamp):
            out.append(line)
    return out


def slate_line(lines, target_date) -> str | None:
    """What the morning scout said it was pricing, or None if it never said.

    "nothing scheduled" is an ANSWER -- an empty card. None means the line
    never appeared at all, which is a broken scout. Conflating them would
    turn a quiet Tuesday into a false alarm and a dead scout into silence.
    """
    stamp = target_date.isoformat()
    for line in reversed(list(lines)):
        m = _SLATE_RE.search(line)
        if m and m.group(1) == stamp:
            return m.group(2)
    return None


def classify(lines, target_date, *, picks_now: int,
             et_hour: int | None = None,
             send_hour: int = DEFAULT_SEND_HOUR_ET) -> Result:
    """Decide which state today is in.

    ``et_hour`` is the current hour in Eastern. Before the send hour, the
    ABSENCE of a digest line means nothing -- it has not been written yet --
    so it is reported as TOO_EARLY rather than as a fault. A check that
    alarms whenever it is run early is one people learn to ignore, which is
    the same failure as not having it. Absence is excused; a line that
    already exists is still read.
    """
    lines = list(lines)
    slate = slate_line(lines, target_date)
    relevant = digest_lines(lines, target_date)
    notes = []
    if slate is None:
        notes.append("no 'Morning slate' line for today -- the 8am scout may "
                     "not have run; that line is logged even for an empty card")

    if not relevant and et_hour is not None and et_hour <= send_hour:
        return Result(Outcome.TOO_EARLY, True, slate, picks_now,
                      f"it is {et_hour}:00 ET and the digest fires at "
                      f"{send_hour}:00 ET -- nothing to judge yet")

    if not relevant:
        return Result(Outcome.NEVER_RAN, False, slate, picks_now,
                      "; ".join(notes + [
                          "no digest line for today at all: the scheduler was "
                          "down, or it died before 11:00 ET"]))

    if any(_SENT_RE.search(x) for x in relevant):
        return Result(Outcome.SENT, True, slate, picks_now,
                      "; ".join(notes) or "digest sent")

    if picks_now > 0:
        return Result(
            Outcome.EMPTY_BUT_PICKS_EXIST, False, slate, picks_now,
            "; ".join(notes + [
                f"digest found nothing at 11:00 but {picks_now} pick(s) exist "
                f"for today now -- the morning slate is landing too late, "
                f"which is the regression d30016c fixed"]))

    return Result(Outcome.EMPTY_QUIET, True, slate, picks_now,
                  "; ".join(notes + [
                      "digest was empty and no picks exist for today even "
                      "now: a genuinely quiet card, not a fault"]))


def picks_for(db_path: str, target_date) -> int:
    """How many non-prop picks exist for `target_date` right now."""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "select count(*) from picks p join games g on g.id = p.game_id "
            "where g.date = ? and p.pick_type != 'prop'",
            (target_date.isoformat(),)).fetchone()[0]
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--db", default=os.path.join(REPO, "sports_picks.db"))
    ap.add_argument("--date", help="ISO date. Defaults to today in ET.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.date:
        target = datetime.date.fromisoformat(args.date)
    else:
        from backend.time_utils import et_today
        target = et_today()

    try:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        print(f"CANNOT READ LOG {args.log}: {type(e).__name__}")
        return 1

    from backend.time_utils import ET
    now_et = datetime.datetime.now(tz=ET)
    result = classify(lines, target, picks_now=picks_for(args.db, target),
                      et_hour=now_et.hour if now_et.date() == target else None)

    if args.json:
        print(json.dumps({"date": target.isoformat(),
                          "outcome": result.outcome.value, "ok": result.ok,
                          "slate": result.slate, "picks_now": result.picks_now,
                          "detail": result.detail}, indent=2))
    else:
        print(f"[{target}] {'OK' if result.ok else 'PROBLEM'}: "
              f"{result.outcome.value}")
        print(f"  morning slate : "
              f"{result.slate if result.slate is not None else '(line absent)'}")
        print(f"  picks for today now: {result.picks_now}")
        print(f"  {result.detail}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
