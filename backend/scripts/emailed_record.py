"""How have the picks the digest actually emailed done?

    python -m backend.scripts.emailed_record
    python -m backend.scripts.emailed_record --days 30

Read-only. Grades each emailed pick as it was sent (see backend.digest.record),
at the emailed price, one unit per pick. Recording started 2026-09-28; earlier
digests were never stored and are not in this report.
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.digest.record import emailed_record
from backend.time_utils import et_today


def format_rows(rows) -> str:
    if not rows:
        return "No emailed picks recorded yet."
    head = f"{'sport':<7}{'kind':<6}{'W-L-P':>10}{'win %':>8}{'units':>9}{'pending':>9}"
    lines = [head, "-" * len(head)]
    for r in rows:
        wlp = f"{r.wins}-{r.losses}-{r.pushes}"
        pct = "—" if r.win_pct is None else f"{r.win_pct:.1%}"
        lines.append(f"{r.sport:<7}{r.kind:<6}{wlp:>10}{pct:>8}"
                     f"{r.units:>+9.2f}{r.pending:>9}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, help="Only digests from the last N days.")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    session = get_session(get_engine(config["database_path"]))
    try:
        since = et_today() - timedelta(days=args.days) if args.days else None
        print(format_rows(emailed_record(session, since=since)))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
