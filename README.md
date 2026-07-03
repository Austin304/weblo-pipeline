# Web-Design Lead Pipeline

Python pipeline that finds local businesses with bad/no websites, builds each a
tailored sample site, cold-emails the sample, and notifies the operator on
Telegram when someone is interested. Specs live in the parent folder (docs
00-09); this folder is the implementation.

## Layout

| File | Job |
|---|---|
| `config.py` | loads `.env` (from here or the parent folder) |
| `db.py` | SQLite state layer, WAL mode (doc 04) |
| `costs.py` | spend ledger + budget gate — every paid call checks first (doc 08) |
| `find_leads.py` | stage 1: Places search → qualify → email discovery (find-leads-brief) |
| `build_sample.py` | stage 2: niche briefs + archetypes + multi-pass Claude generation (doc 01) |
| `serve_samples.py` | long-running: serves samples + logs visits (fronted by Cloudflare Tunnel) |
| `write_email.py` | Claude cold-email + follow-up copy (email-writing-instructions) |
| `send_email.py` | Gmail send with cap/pacing/window/suppression (email-agent-instructions) |
| `watch_replies.py` | long-running: reply classification, Telegram alerts + commands (doc 02) |
| `run.py` | cron entrypoint: `find` / `build` / `send` / `followups` / `status` |

Deferred (by design, month-one scope cut): postcards (doc 06), automated full
build (doc 03), metrics module (doc 09) beyond the built-in bounce guard.

## First-time setup

```bash
python -m pip install -r requirements.txt
python db.py                # creates leads.db
python run.py status
```

Email track additionally needs, in `.env`: `SENDER_EMAIL`, `SENDER_NAME`,
`GMAIL_OAUTH_CREDENTIALS` (OAuth client JSON; first send opens a browser to
mint `token.json`), `DAILY_SEND_CAP`, `BUSINESS_MAILING_ADDRESS`.

## Calibration workflow (before any real sends)

1. `python run.py find` — pull + qualify the first territory
2. `python run.py build 30` — build ~30 samples
3. Hand-grade every one (serve locally: `python serve_samples.py`, then open
   `http://localhost:8788/<slug>`). Tune prompts in `build_sample.py` until
   consistently good.
4. `python run.py send --dry-run` — inspect generated email copy in the DB
5. Only then: real sends, 10/day, ramping ~20%/week (doc 05 step 7)

## VM deploy (doc 07)

cron:
```
0 6 * * 1   cd /opt/pipeline && python3 run.py find
15 * * * *  cd /opt/pipeline && python3 run.py build
*/20 * * * * cd /opt/pipeline && python3 run.py send
0 16 * * *  cd /opt/pipeline && python3 run.py followups
```
systemd services: `serve_samples.py` (behind `cloudflared`) and `watch_replies.py`.
