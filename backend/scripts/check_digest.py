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

#: Where this script records its own verdicts.
HEALTH_LOG = os.path.join(REPO, "digest_health.log")

#: Rotate the health log past this size, keeping KEEP older generations.
#: At roughly 400 bytes an entry that is over a year per file.
MAX_BYTES = 128 * 1024
KEEP = 3

_SENT_RE = re.compile(r"Digest sent to (\d+) recipient")
_EMPTY_RE = re.compile(r"Digest for (\d{4}-\d{2}-\d{2}) is empty")
_SLATE_RE = re.compile(r"Morning slate for (\d{4}-\d{2}-\d{2}): (.+?)\s*$")


#: Hour (ET) the digest job fires. Mirrors config digest.send_hour_et.
DEFAULT_SEND_HOUR_ET = 11


def rotate(path: str, *, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> None:
    """Move `path` aside once it exceeds `max_bytes`, keeping `keep` older ones.

    Generations shift down: .2 -> .3, .1 -> .2, current -> .1, and only the
    generation past `keep` is removed.

    `start_scheduler.ps1` rotates scheduler.log to a single .prev, so every
    restart destroys the previous one -- usually the log covering whatever is
    being investigated. A health record exists to answer "has this failed
    before?", which one generation cannot do, so this keeps several and drops
    only the oldest.
    """
    if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
        return

    oldest = f"{path}.{keep}"
    if os.path.exists(oldest):
        os.remove(oldest)
    # Downwards, so a generation is never overwritten before it has moved.
    for n in range(keep - 1, 0, -1):
        src = f"{path}.{n}"
        if os.path.exists(src):
            os.replace(src, f"{path}.{n + 1}")
    os.replace(path, f"{path}.1")


def append_entry(path: str, body: str, *, exit_code: int = 0,
                 max_bytes: int = MAX_BYTES, keep: int = KEEP) -> None:
    """Append one dated entry, rotating first if the log has grown.

    Rotation happens here rather than on a schedule of its own, so there is
    no way to write to an unrotated log.
    """
    rotate(path, max_bytes=max_bytes, keep=keep)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"===== {stamp} (exit {exit_code}) =====\n{body}\n")


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
                append_entry(args.health_log, text, exit_code=code)
            except OSError as e:
                # Never let the record-keeping fail the check itself.
                print(f"(could not write {args.health_log}: {type(e).__name__})")
        return code

    try:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return _emit(f"CANNOT READ LOG {args.log}: {type(e).__name__}", 1)

    from backend.time_utils import ET
    now_et = datetime.datetime.now(tz=ET)
    result = classify(lines, target, picks_now=picks_for(args.db, target),
                      et_hour=now_et.hour if now_et.date() == target else None)

    if args.json:
        report = json.dumps({"date": target.isoformat(),
                             "outcome": result.outcome.value, "ok": result.ok,
                             "slate": result.slate,
                             "picks_now": result.picks_now,
                             "detail": result.detail}, indent=2)
    else:
        slate = result.slate if result.slate is not None else "(line absent)"
        report = "\n".join([
            f"[{target}] {'OK' if result.ok else 'PROBLEM'}: "
            f"{result.outcome.value}",
            f"  morning slate : {slate}",
            f"  picks for today now: {result.picks_now}",
            f"  {result.detail}"])
    return _emit(report, result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
