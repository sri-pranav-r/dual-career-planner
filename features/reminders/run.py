"""
Run the daily reminders outside Streamlit, e.g. from cron or Windows Task Scheduler.
From the planner folder:

    python3 -m features.reminders.run              # once per day; a second call the same day does nothing
    python3 -m features.reminders.run --force      # run again now (already-sent reminders are still skipped)
    python3 -m features.reminders.run --report     # also build this week's team load reports
    python3 -m features.reminders.run --db other.db --today 2026-10-05

cron, every day at 7am:  0 7 * * *  cd /path/to/planner && python3 -m features.reminders.run
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import data

from . import logic


def main(argv=None) -> dict | None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database path (default: env PLANNER_DB or planner.db)")
    ap.add_argument("--today", type=date.fromisoformat, help="pretend today is YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="run even if today's run already happened")
    ap.add_argument("--report", action="store_true", help="build the weekly team load reports now")
    args = ap.parse_args(argv)
    con = data.connect(args.db)
    if args.force or args.report:
        out = logic.run_all(con, args.today, report=True if args.report else None)
    else:
        out = logic.run_if_due(con, args.today)
    print(json.dumps(out, indent=2) if out else "Already ran today; use --force to run again.")
    return out


if __name__ == "__main__":
    main()
