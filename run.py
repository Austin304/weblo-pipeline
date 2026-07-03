"""Scheduler entrypoint (doc 04). Cron calls one job per invocation:

  python run.py find                 # weekly top-up (standing campaign config)
  python run.py build [limit]        # hourly: build samples for QUALIFIED leads
  python run.py send [--dry-run]     # every few minutes, business hours
  python run.py followups [--dry-run]
  python run.py status               # print funnel counts + MTD spend

The two long-running processes are separate systemd services:
  python serve_samples.py   and   python watch_replies.py
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

import config
import db


def setup_logging(job: str):
    config.LOGS_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        config.LOGS_DIR / f"{job}.log", maxBytes=2_000_000, backupCount=3)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    job = sys.argv[1]
    dry = "--dry-run" in sys.argv
    setup_logging(job)
    db.init_db()

    if job == "find":
        import find_leads
        print(find_leads.top_up())
    elif job == "build":
        import build_sample
        limit = next((int(a) for a in sys.argv[2:] if a.isdigit()), 10)
        print(build_sample.build_samples(limit))
    elif job == "send":
        import send_email
        print(send_email.send_batch(dry_run=dry))
    elif job == "followups":
        import send_email
        print(send_email.send_followups(dry_run=dry))
    elif job == "status":
        with db.connect() as conn:
            import costs
            for status, n in db.counts_by_status(conn).items():
                print(f"{status:20s} {n}")
            print(f"{'MTD spend':20s} ${costs.month_to_date(conn):.2f}"
                  f" / ${config.PIPELINE_MONTHLY_BUDGET_USD:.0f}")
            print(f"{'Sent today':20s} {db.sent_today(conn)} / {config.DAILY_SEND_CAP}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
