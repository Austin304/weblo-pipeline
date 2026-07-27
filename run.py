"""Scheduler entrypoint (doc 04). Cron calls one job per invocation:

  python run.py find                 # weekly top-up (standing campaign config)
  python run.py build [limit]        # hourly: build samples for QUALIFIED leads
  python run.py draft [limit]        # calibration: write+print emails, NO send
  python run.py send [--dry-run]     # every few minutes, business hours
                                     #   (with SEND_REQUIRE_APPROVAL on, stages each
                                     #    email to Telegram for a phone tap instead
                                     #    of auto-sending; --dry-run prints copy)
  python run.py queue [limit]        # force the phone-approval staging path
  python run.py followups [--dry-run]
  python run.py auth                 # mint/verify Gmail token.json (opens a browser
                                     # on first run) — do this once before the daemons
  python run.py status               # print funnel counts + MTD spend
  python run.py grade <id> <ABCDEFG> <overall> [note] [--vision ABCDEFG:o]
  python run.py review <id ...>      # laptop: batch vision-grade -> ONE local
                                     # review page (prefilled buttons, copy-all)
  python run.py vision <id ...>      # laptop: screenshot + AI vision grade (assist)
  python run.py vision --url <url> [context]            # grade any live sample URL
  python run.py insights             # grade averages by niche/imagery/archetype + notes
  python run.py exemplar <id>        # distill a top-graded sample into a style crib

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
        rest = sys.argv[2:]
        if rest and rest[0] == "id":  # build specific leads: build id 12 27 ...
            ids = [int(a) for a in rest[1:] if a.isdigit()]
            stats = {"built": 0, "failed": 0}
            with db.connect() as conn:
                for lid in ids:
                    lead = db.get_lead(conn, lid)
                    if lead is None:
                        print(f"lead {lid} not found")
                        continue
                    try:
                        ok = build_sample.build_one(conn, lead)
                        stats["built" if ok else "failed"] += 1
                        row = db.get_lead(conn, lid)
                        print(f"lead {lid} ({lead['business_name']}): "
                              f"{'BUILT ' + (row['sample_url'] or '') if ok else 'FAILED'}")
                    except Exception:
                        logging.getLogger(__name__).exception("build_one crashed %s", lid)
                        stats["failed"] += 1
            print(stats)
        else:
            limit = next((int(a) for a in rest if a.isdigit()), 10)
            print(build_sample.build_samples(limit))
    elif job == "draft":
        import send_email
        limit = next((int(a) for a in sys.argv[2:] if a.isdigit()), 30)
        send_email.draft_emails(limit=limit, regenerate="--regenerate" in sys.argv)
    elif job in ("send", "queue"):
        import send_email
        limit = next((int(a) for a in sys.argv[2:] if a.isdigit()), None)
        # With the manual phone-approval gate on (config.SEND_REQUIRE_APPROVAL),
        # `send` STAGES to Telegram for a tap instead of auto-sending — so the
        # existing cron `send` line never fires an unreviewed email. `--dry-run`
        # still prints the auto-mode copy; `queue` forces staging regardless.
        if job == "queue" or (config.SEND_REQUIRE_APPROVAL and not dry):
            print(send_email.queue_batch(limit=limit))
        else:
            print(send_email.send_batch(dry_run=dry))
    elif job == "followups":
        import send_email
        print(send_email.send_followups(dry_run=dry))
    elif job == "auth":
        # One-liner to mint/verify token.json before starting the daemons. On a
        # fresh box gmail_service() opens a browser for consent (needs credentials.json
        # + SENDER_EMAIL/SENDER_NAME); on a headless VM, mint on the laptop and copy
        # token.json over. getProfile() confirms the token can actually reach Gmail.
        import send_email
        if not (config.SENDER_EMAIL and config.SENDER_NAME):
            print("Gmail auth NOT possible yet: set SENDER_EMAIL and SENDER_NAME in .env first.")
            sys.exit(1)
        try:
            svc = send_email.gmail_service()
            email = svc.users().getProfile(userId="me").execute().get("emailAddress")
            print(f"Gmail auth OK — token.json valid, authenticated as {email}.")
        except Exception as e:
            print(f"Gmail auth FAILED: {e}")
            print("Check that credentials.json (OAuth client) is present at the repo "
                  "root and the OAuth consent/scopes are correct, then retry.")
            sys.exit(1)
    elif job == "grade":
        # grade <lead_id> <ABCDEFG scores, each 1-5> <overall /10> [note...]
        #       [--vision ABCDEFG:overall]   (the AI pre-grade, for calibration)
        rest = sys.argv[2:]
        vision = None
        if "--vision" in rest:
            i = rest.index("--vision")
            if i + 1 >= len(rest):
                print("--vision needs a value like 5443454:7")
                sys.exit(1)
            vision = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        if len(rest) < 3:
            print("usage: run.py grade <lead_id> <ABCDEFG> <overall> [note] [--vision ABCDEFG:o]\n"
                  "  ABCDEFG = 7 digits 1-5 (hero design layout imagery copy trust beats)\n"
                  "  example: run.py grade 18 5443454 7 'CHANGE: kill hero badge | KEEP: real photos'")
            sys.exit(1)
        lead_id = int(rest[0])
        scores = rest[1]
        if len(scores) != 7 or any(c not in "12345" for c in scores):
            print(f"bad scores {scores!r}: need exactly 7 digits, each 1-5")
            sys.exit(1)
        factors = [int(c) for c in scores]
        overall = int(rest[2])
        notes = " ".join(rest[3:])
        with db.connect() as conn:
            lead = db.get_lead(conn, lead_id)
            if lead is None:
                print(f"lead {lead_id} not found")
                sys.exit(1)
            db.record_grade(conn, lead_id, factors, overall,
                            notes, lead["sample_slug"], vision=vision)
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
    elif job == "review":
        import calibrate
        calibrate.review_command(sys.argv[2:])
    elif job == "vision":
        import calibrate
        calibrate.vision_command(sys.argv[2:])
    elif job == "insights":
        import calibrate
        calibrate.print_insights()
    elif job == "exemplar":
        if len(sys.argv) < 3 or not sys.argv[2].isdigit():
            print("usage: run.py exemplar <lead_id>   (grade it first; only "
                  "distill samples you'd be proud to send)")
            sys.exit(1)
        import calibrate
        calibrate.save_exemplar(int(sys.argv[2]))
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
