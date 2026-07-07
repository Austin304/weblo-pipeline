"""Scheduler entrypoint (doc 04). Cron calls one job per invocation:

  python run.py find                 # weekly top-up (standing campaign config)
  python run.py build [limit]        # hourly: build samples for QUALIFIED leads
  python run.py draft [limit]        # calibration: write+print emails, NO send
  python run.py send [--dry-run]     # every few minutes, business hours
  python run.py followups [--dry-run]
  python run.py status               # print funnel counts + MTD spend
  python run.py grade <id> <ABCDEFG> <overall> [note]   # log a manual sample grade

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
    elif job == "draft":
        import send_email
        limit = next((int(a) for a in sys.argv[2:] if a.isdigit()), 30)
        send_email.draft_emails(limit=limit, regenerate="--regenerate" in sys.argv)
    elif job == "send":
        import send_email
        print(send_email.send_batch(dry_run=dry))
    elif job == "followups":
        import send_email
        print(send_email.send_followups(dry_run=dry))
    elif job == "grade":
        # grade <lead_id> <ABCDEFG scores, each 1-5> <overall /10> [note...]
        if len(sys.argv) < 5:
            print("usage: run.py grade <lead_id> <ABCDEFG> <overall> [note]\n"
                  "  ABCDEFG = 7 digits 1-5 (hero design layout imagery copy trust beats)\n"
                  "  example: run.py grade 18 5443454 7 'CHANGE: kill hero badge | KEEP: real photos'")
            sys.exit(1)
        lead_id = int(sys.argv[2])
        scores = sys.argv[3]
        if len(scores) != 7 or any(c not in "12345" for c in scores):
            print(f"bad scores {scores!r}: need exactly 7 digits, each 1-5")
            sys.exit(1)
        factors = [int(c) for c in scores]
        overall = int(sys.argv[4])
        notes = " ".join(sys.argv[5:])
        with db.connect() as conn:
            lead = db.get_lead(conn, lead_id)
            if lead is None:
                print(f"lead {lead_id} not found")
                sys.exit(1)
            db.record_grade(conn, lead_id, factors, overall,
                            notes, lead["sample_slug"])
            conn.commit()
            labels = ["hero", "design", "layout", "imagery", "copy", "trust", "beats"]
            print(f"graded lead {lead_id} ({lead['business_name']}), "
                  f"build {lead['sample_slug']}:")
            print("  " + "  ".join(f"{l}={s}" for l, s in zip(labels, factors))
                  + f"  | overall={overall}/10")
            if notes:
                print(f"  {notes}")
            avg = db.grade_averages(conn)
            if avg.get("n"):
                print(f"\nacross {avg['n']} graded sample(s):")
                print("  " + "  ".join(
                    f"{l}={avg[l]:.1f}" for l in (*labels, "overall") if avg.get(l) is not None))
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
