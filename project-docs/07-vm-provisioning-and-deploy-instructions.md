# 07 — VM Provisioning & Deploy (The Box Everything Runs On)

## Objective
Stand up the **always-on machine** that hosts the whole pipeline: the SQLite state, the two long-running processes (`serve_samples.py`, `watch_replies.py`), the Cloudflare Tunnel, and the `cron`-driven batch jobs (doc 04). The decision (doc 00) is a **GCP `e2-micro` on the always-free tier** — `$0/mo`, runs forever. This doc is the runbook to get it live. Do it on **day one, in parallel with doc 05** (the 2-week warm-up is the long pole).

> Read doc 00 (system overview) and doc 04 (orchestration) first — this doc only covers provisioning and deploy, not what the code does.

---

## Decisions already made (do not re-litigate)

| Area | Decision |
|---|---|
| Host | **GCP `e2-micro`, always-free tier.** Region **must** be `us-west1`, `us-central1`, or `us-east1` — the free tier exists only there. |
| OS | **Debian 12 (bookworm)** — ships Python 3.11, minimal, well-supported. |
| Disk | **30 GB standard persistent disk** (the free-tier max; `leads.db` + samples are megabytes). **Not** SSD — SSD isn't free. |
| Inbound network | **None.** The Cloudflare Tunnel is outbound-only, so no inbound ports and **no reserved static IP** (a reserved IP bills — never add one). SSH only, ideally via IAP. |
| Process model | **`systemd`** keeps the two daemons alive (auto-restart on crash); **`cron`** runs the batch jobs by calling `run.py <job>`. |
| App location | `/opt/pipeline` (the repo from doc 00's layout), run as a dedicated `weblo` user under a venv at `/opt/pipeline/.venv`. |
| Scraping | `requests` + `beautifulsoup4` only — **do not** `pip install playwright`; Chromium won't fit ~1 GB RAM (doc 00). |

---

## Free-tier guardrails (what keeps it $0)
- **One** `e2-micro`, in a free region. A second instance, or a bigger type, bills.
- Standard persistent disk ≤ 30 GB. No SSD, no extra disks.
- **No reserved/static external IP** — the tunnel makes one unnecessary, and an idle reserved IP is billed.
- Use **Standard** network tier, not Premium.
- Egress is 1 GB/mo free (North America); serving tiny HTML samples stays far under it.
- A billing account must be on file. If you ever provision outside these limits, *that part* bills — the box itself stays free. Set a **GCP Budget alert at $1** so any accidental spend pings you immediately.
- (Unlike Oracle's free tier, GCP does **not** reclaim idle always-free instances — no need to keep it artificially busy.)

---

## STEP 1 — Create the VM
In the GCP Console → Compute Engine → Create instance (or `gcloud`):
- **Name:** `weblo-pipeline`
- **Region/Zone:** a free-tier region (e.g. `us-central1-a`)
- **Machine type:** `e2-micro`
- **Boot disk:** Debian 12, **30 GB standard** persistent disk
- **Firewall:** leave **both** "Allow HTTP/HTTPS" **unchecked** — nothing inbound is needed.

`gcloud` equivalent:
```bash
gcloud compute instances create weblo-pipeline \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard
```

---

## STEP 2 — Access & lock-down
- SSH in via the Console's SSH button or `gcloud compute ssh weblo-pipeline --zone=us-central1-a`.
- **Keep the default firewall closed to inbound except SSH.** Prefer **IAP-based SSH** (no public SSH port) if you want it tighter. No other inbound rule is ever needed — the tunnel is outbound.
- Leave the server clock in **UTC** (default). The DB stores UTC (doc 04); per-recipient send windows are computed in code from each lead's `timezone` column, so the server's timezone is irrelevant to the 9am–5pm rule.

---

## STEP 3 — Base setup
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 python3-venv python3-pip git sqlite3

# dedicated service user + app dir
sudo useradd --system --create-home --shell /usr/sbin/nologin weblo
sudo mkdir -p /opt/pipeline && sudo chown weblo:weblo /opt/pipeline

# put the code at /opt/pipeline (git clone, scp, or rsync the repo from doc 00)
# then create the venv and install deps (NO playwright):
sudo -u weblo python3 -m venv /opt/pipeline/.venv
sudo -u weblo /opt/pipeline/.venv/bin/pip install \
  anthropic google-api-python-client google-auth google-auth-oauthlib \
  requests httpx beautifulsoup4 lxml flask lob qrcode[pil]
mkdir -p /opt/pipeline/logs /opt/pipeline/samples_cache
```
> Pin versions in a `requirements.txt` and `pip install -r` it instead, so deploys are reproducible. `playwright` is intentionally absent (doc 00).

---

## STEP 4 — Config + database
- Place the filled-in **`.env`** at `/opt/pipeline/.env`, owned by `weblo`, mode `600` (`chmod 600 /opt/pipeline/.env`) so only the service user can read the secrets.
- Initialize the DB once (creates `leads.db` + tables, sets WAL mode per doc 04):
```bash
sudo -u weblo bash -c 'cd /opt/pipeline && .venv/bin/python -c "import db; db.init()"'
```
- WAL mode (doc 04) is what lets `cron` jobs, the reply daemon, and the samples app read/write `leads.db` concurrently without locking.

---

## STEP 5 — Cloudflare Tunnel (`cloudflared`)
Exposes the local samples app at `samples.webloapp.com` with no inbound ports.
```bash
# install cloudflared (Debian repo)
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared bookworm main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt -y install cloudflared

# authenticate + create the named tunnel (CLOUDFLARE_TUNNEL_NAME=samples in .env)
cloudflared tunnel login
cloudflared tunnel create samples
cloudflared tunnel route dns samples samples.webloapp.com
```
Create `/etc/cloudflared/config.yml` (point it at `SAMPLES_PORT=8788`):
```yaml
tunnel: samples
credentials-file: /root/.cloudflared/<TUNNEL-ID>.json   # path printed by `tunnel create`
ingress:
  - hostname: samples.webloapp.com
    service: http://localhost:8788
  - service: http_status:404
```
Install it as a service so it survives reboots:
```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

---

## STEP 6 — The two daemons as `systemd` services
These are the long-running processes from doc 04 — both auto-restart on crash.

`/etc/systemd/system/weblo-samples.service`:
```ini
[Unit]
Description=Weblo samples web app (serve_samples.py)
After=network-online.target cloudflared.service
Wants=network-online.target

[Service]
User=weblo
WorkingDirectory=/opt/pipeline
EnvironmentFile=/opt/pipeline/.env
ExecStart=/opt/pipeline/.venv/bin/python serve_samples.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/weblo-replies.service`:
```ini
[Unit]
Description=Weblo reply watcher (watch_replies.py)
After=network-online.target
Wants=network-online.target

[Service]
User=weblo
WorkingDirectory=/opt/pipeline
EnvironmentFile=/opt/pipeline/.env
ExecStart=/opt/pipeline/.venv/bin/python watch_replies.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Enable both:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now weblo-samples weblo-replies
```

---

## STEP 7 — The batch jobs as `cron`
`cron` is the scheduler; each entry just invokes `run.py <job>` (doc 04). Install under the `weblo` user (`sudo -u weblo crontab -e`). Times are UTC — that's fine because the `send_emails` job enforces the per-lead 9am–5pm **local** window, the daily cap, and 60–120s spacing in code (doc 04 + email-agent-instructions), not via cron timing.

```cron
# m   h        dom mon dow   command
*/3   *        *   *   *     cd /opt/pipeline && .venv/bin/python run.py send_emails   >> logs/cron.log 2>&1
17    *        *   *   *     cd /opt/pipeline && .venv/bin/python run.py build_samples >> logs/cron.log 2>&1
30    13       *   *   1     cd /opt/pipeline && .venv/bin/python run.py find_leads    >> logs/cron.log 2>&1
0     14       *   *   *     cd /opt/pipeline && .venv/bin/python run.py followups     >> logs/cron.log 2>&1
0     15,19    *   *   *     cd /opt/pipeline && .venv/bin/python run.py mail_job      >> logs/cron.log 2>&1
```
- `send_emails` runs every 3 min and **early-exits** when nothing is eligible (outside the cap, outside local hours, or no `email_status='found'` leads) — cheap, and it's what makes the local-time window + spacing precise.
- `build_samples` hourly, `find_leads` weekly (Mon), `followups` daily, `mail_job` twice daily — matching doc 04's cadence table.
- Each job must be **idempotent** (doc 04): select by status, check `emailed_at`/`gmail_message_id`/`mail_pieces` before acting, so a re-run never double-sends.

---

## STEP 8 — Logging & resilience
- Jobs and daemons write structured logs to `/opt/pipeline/logs` (doc 04). Add a `logrotate` rule so they don't fill the disk:
  `/etc/logrotate.d/weblo` → rotate `/opt/pipeline/logs/*.log` weekly, keep 4, compress.
- `systemd` already restarts the daemons on crash; `journalctl -u weblo-replies -u weblo-samples -u cloudflared` shows their output.
- The reply daemon resumes from the last-seen message id on restart (doc 02); the samples app is stateless beyond the DB.

---

## STEP 9 — Verify it's actually live
- `systemctl status cloudflared weblo-samples weblo-replies` → all **active (running)**.
- `curl -I https://samples.webloapp.com/<any-test-slug>` → **HTTP 200** through the tunnel (a built sample), and a row appears in `sample_visits`.
- Trigger one job by hand: `sudo -u weblo bash -c 'cd /opt/pipeline && .venv/bin/python run.py build_samples'` → check `logs/`.
- Confirm a **Telegram** test message arrives (the `watch_replies.py` command handler / a notify ping).
- Reboot the VM (`sudo reboot`) and re-check Step 9 — everything must come back on its own.

---

## Done-when
- `e2-micro` is running in a free-tier region on a 30 GB standard disk, no inbound firewall rule, no static IP, with a $1 budget alert set.
- `cloudflared`, `weblo-samples`, and `weblo-replies` are enabled `systemd` services and survive a reboot.
- `samples.webloapp.com/<slug>` returns 200 through the tunnel and logs a `sample_visits` row.
- The `weblo` crontab runs all five jobs; each is idempotent and logs to `/opt/pipeline/logs`.
- `.env` is mode `600`, owned by `weblo`; the DB is initialized in WAL mode.
- `playwright`/Chromium is **not** installed.
