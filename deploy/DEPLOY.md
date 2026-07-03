# VM Deploy Runbook

The pipeline runs on the always-free GCP `e2-micro` (`weblo-pipeline`,
`us-central1-a`, Debian 12) that's already provisioned and running. This gets
the code onto it and stands up every service. Doc 07 is the full spec; this is
the actual steps.

Almost all of it is automated by [`bootstrap.sh`](bootstrap.sh). The only parts
that need **you** are the ones requiring your Google login: connecting to the
VM and copying files. Everything after that is one command.

---

## What you run (≈5 min)

### 1. Connect + copy the code

The laptop has no `gcloud` and no SSH key for the VM, so use the **GCP Console
SSH button** (Compute Engine → VM instances → `weblo-pipeline` → SSH), or
install the SDK and `gcloud compute ssh weblo-pipeline --zone=us-central1-a`.

Easiest path that needs no local tooling — from the **Console SSH window**, pull
the code straight onto the VM. Two sub-options:

**a) If you push this repo to a private git remote:**
```bash
sudo mkdir -p /opt/pipeline && sudo chown "$USER" /opt/pipeline
git clone <your-repo-url> /tmp/site && cp -r /tmp/site/pipeline/. /opt/pipeline/
```

**b) Or upload from the laptop** — in the Console SSH window use the gear menu →
"Upload file" to send a zip of the `pipeline/` folder, then unzip into
`/opt/pipeline`.

Either way you must also place the filled **`.env`** at `/opt/pipeline/.env`
(it's gitignored, so it won't come from git — upload it separately via the same
Upload-file menu, or paste it with `nano /opt/pipeline/.env`).

Verify before continuing:
```bash
ls /opt/pipeline/run.py /opt/pipeline/.env    # both must exist
```

### 2. Run the bootstrap

```bash
sudo bash /opt/pipeline/deploy/bootstrap.sh
```

That's it. It installs packages, creates the `weblo` service user + venv,
initializes the DB, installs `cloudflared`, **creates the Cloudflare tunnel and
DNS route via the API** (no browser login — uses `CLOUDFLARE_API_TOKEN` from
`.env`), installs the two daemons + `cloudflared` as systemd services, and sets
up cron + logrotate. It's idempotent — safe to re-run if a step fails.

### 3. Confirm it's live

Wait ~1 minute for DNS, then from anywhere:
```bash
curl -I https://samples.webloapp.com/healthz     # expect HTTP/2 200
```
On the VM:
```bash
systemctl status cloudflared weblo-samples weblo-replies   # all active (running)
sudo reboot          # then re-check the above — everything must come back on its own
```

---

## What bootstrap does NOT do (still your steps)

- **Gmail OAuth.** The email track needs `credentials.json` (OAuth client) at
  `/opt/pipeline/` and a one-time browser consent to mint `token.json`. Because
  the VM is headless, easiest is to run the first auth on your laptop
  (`python run.py send --dry-run` won't need it, but the first real
  `gmail_service()` call opens a browser), then copy the resulting `token.json`
  to `/opt/pipeline/token.json`. Until `SENDER_EMAIL`/`SENDER_NAME` are set and
  `token.json` exists, the send job simply no-ops (it logs and exits) — the rest
  of the pipeline still runs.
- **The calibration pass.** Before real sends: `run.py find`, `run.py build 30`,
  and hand-grade the samples (see the top-level README).

---

## If bootstrap fails at the Cloudflare step

The script dies with the exact CF API error. The most likely cause is the API
token lacking **write** scope (it's confirmed to have read). If you see a
permission error on tunnel-create or DNS-route, edit the token at
Cloudflare → My Profile → API Tokens to include **Account · Cloudflare Tunnel ·
Edit** and **Zone · DNS · Edit** for `webloapp.com`, then re-run bootstrap
(it's idempotent).

---

## Rollback / teardown

```bash
sudo systemctl disable --now weblo-samples weblo-replies cloudflared
sudo cloudflared service uninstall
sudo crontab -u weblo -r
```
The tunnel + DNS record can be removed from the Cloudflare dashboard (Zero Trust
→ Networks → Tunnels, and DNS → the `samples` CNAME).
