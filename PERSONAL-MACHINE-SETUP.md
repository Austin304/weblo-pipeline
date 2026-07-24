# Personal machine — clean-sweep setup

Everything the pipeline needs falls into 4 buckets. Do them in order and it'll run clean.

## 1. Code + docs  → `git clone` (nothing to copy by hand)
All source, project-docs, and `exemplars/` are in your personal repo:

    git clone https://github.com/Austin304/weblo-pipeline.git
    cd weblo-pipeline
    python -m venv .venv
    # Windows:  .venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
    pip install -r requirements.txt

## 2. Database  → copy the ONE file
- Take `leads_transfer.db` from the work machine.
- Drop it in the repo root on the personal machine and **rename it to `leads.db`**.
- (238 leads, 822 spend-ledger rows, 51 transitions, 11 grades — verified copy.)

## 3. Built samples  → copy the folder (OPTIONAL — regenerable)
- Copy `samples_cache/` (20 built sample sites, ~377 KB) if you want to keep the work already generated.
- If you skip it, the pipeline just rebuilds samples on demand. Not required to run.

## 4. Secrets  → NOT on the work machine; recreate these yourself
These 3 files are gitignored and do **not** exist anywhere on the work laptop, so there's nothing to copy — you create them fresh:

### a) `.env`
Copy `project-docs/env-template.txt` to a file named `.env` in the repo root. The config values are pre-filled; fill in these keys from each service's console:

| Key | Where to get it | Needed for |
|-----|-----------------|-----------|
| `MOONSHOT_API_KEY` | platform.moonshot.ai | LLM core (default provider = Kimi): sample builds, email/postcard copy, reply classification |
| `GOOGLE_PLACES_API_KEY` | Google Cloud console | finding leads (core) |
| `TELEGRAM_BOT_TOKEN` | @BotFather | notifications |
| `CLOUDFLARE_API_TOKEN` | Cloudflare dash | serving samples over the tunnel |
| `PEXELS_API_KEY` | pexels.com/api | stock photos (optional) |
| `LOB_API_KEY_TEST` / `LOB_API_KEY_LIVE` | dashboard.lob.com | physical postcards (only if using the mail track) |

The active LLM is set by `LLM_PROVIDER` (default `moonshot`). Optional keys can stay blank — the code
falls back without them: `ANTHROPIC_API_KEY` (only for `LLM_PROVIDER=anthropic`, the A/B flip-back to
Claude), `ZEROBOUNCE_API_KEY`, `UNSPLASH_ACCESS_KEY`, `PIXABAY_API_KEY`.

### b) `credentials.json`  (only if using the email track)
Download the OAuth client from Google Cloud console → APIs & Services → Credentials → place at repo root.

### c) `token.json`  (only if using the email track)
Generated automatically the first time you run the email flow and complete the Google OAuth consent screen. Nothing to copy.

## 5. Verify it's wired up
    bash tools/env-status.sh     # confirms each key is present
    python run.py                # or whichever entrypoint you use

---
**Summary of what to physically move off the work machine:** just `leads_transfer.db` (and optionally the `samples_cache/` folder). Everything else comes from git or gets recreated on the personal side.
