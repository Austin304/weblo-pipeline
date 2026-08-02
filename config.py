"""Load .env and expose all pipeline config as module attributes.

Searches for .env in the pipeline directory first, then the parent
(the docs folder currently holds the real .env). No secrets are ever
printed or logged by this module.
"""
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV_CANDIDATES = [
    Path(os.environ.get("PIPELINE_ENV", "")),
    _HERE / ".env",
    _HERE.parent / ".env",
]


def _load_env() -> dict:
    for candidate in _ENV_CANDIDATES:
        if candidate and candidate.is_file():
            values = {}
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.split(" #")[0].strip().strip('"').strip("'")
                values[key.strip()] = val
            return values
    raise FileNotFoundError(
        ".env not found (looked in pipeline/ and its parent). "
        "Set PIPELINE_ENV to the file's path if it lives elsewhere."
    )


_env = _load_env()


def get(key: str, default=None):
    return os.environ.get(key) or _env.get(key) or default


def get_int(key: str, default: int) -> int:
    try:
        return int(get(key, default))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    try:
        return float(get(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool) -> bool:
    val = get(key, None)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# --- core ---
ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
# LLM provider: "moonshot" (Kimi, default) or "anthropic" (Claude, the flip-back for
# A/B-ing sample quality). Moonshot is OpenAI-compatible, so llm.py drives both.
LLM_PROVIDER = (get("LLM_PROVIDER", "moonshot") or "moonshot").lower()
MOONSHOT_API_KEY = get("MOONSHOT_API_KEY")
MOONSHOT_BASE_URL = get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
# the active provider's key — call-site guards check THIS, not a hard-coded vendor,
# so switching providers doesn't silently disable site-facts / fact-check.
LLM_API_KEY = MOONSHOT_API_KEY if LLM_PROVIDER == "moonshot" else ANTHROPIC_API_KEY
GOOGLE_PLACES_API_KEY = get("GOOGLE_PLACES_API_KEY")
ZEROBOUNCE_API_KEY = get("ZEROBOUNCE_API_KEY")  # optional; MX fallback if blank
# optional free stock-photo APIs (image ladder step 3; all free tiers)
PEXELS_API_KEY = get("PEXELS_API_KEY")
UNSPLASH_ACCESS_KEY = get("UNSPLASH_ACCESS_KEY")
PIXABAY_API_KEY = get("PIXABAY_API_KEY")
DB_PATH = str((_HERE / get("DB_PATH", "leads.db")).resolve()) if not os.path.isabs(get("DB_PATH", "leads.db")) else get("DB_PATH")

# --- samples / tunnel ---
SAMPLES_PORT = get_int("SAMPLES_PORT", 8788)
SAMPLE_BASE_URL = (get("SAMPLE_BASE_URL") or "").rstrip("/")
SAMPLES_CACHE_DIR = _HERE / "samples_cache"
LOGS_DIR = _HERE / "logs"
# Step-0 durable training-data capture (see dataset.py): one permanent per-build dir
# + append-only builds.jsonl. Replaces reliance on the ephemeral logs/bon (overwritten
# by any later build of the same lead — the 278 data-loss in STEP0-HANDOFF §0).
DATASET_DIR = _HERE / "dataset"
# distilled style cribs from top-graded samples + curated grading lessons;
# injected into the generator's system prompt (see build_sample._style_context)
EXEMPLARS_DIR = _HERE / "exemplars"

# --- telegram ---
TELEGRAM_BOT_TOKEN = get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get("TELEGRAM_CHAT_ID")

# --- email track (may be blank until doc 05 ramp begins) ---
SENDER_EMAIL = get("SENDER_EMAIL")
SENDER_NAME = get("SENDER_NAME")
GMAIL_OAUTH_CREDENTIALS = get("GMAIL_OAUTH_CREDENTIALS", str(_HERE / "credentials.json"))
GMAIL_TOKEN_PATH = str(_HERE / "token.json")
DAILY_SEND_CAP = get_int("DAILY_SEND_CAP", 10)
BUSINESS_MAILING_ADDRESS = get("BUSINESS_MAILING_ADDRESS", "")
FOLLOWUP_AFTER_DAYS = get_int("FOLLOWUP_AFTER_DAYS", 6)
# Manual phone-approval gate. When true (the launch default), `run.py send`/`queue`
# STAGES each cold email as PENDING_APPROVAL and pushes it to Telegram with
# Send / Revise / Skip buttons — nothing leaves Gmail until the operator taps Send
# from their phone. Set false to restore fully-automatic sending (doc 05 ramp).
SEND_REQUIRE_APPROVAL = get_bool("SEND_REQUIRE_APPROVAL", True)

# --- standing campaign ---
# ACTIVE NICHE IS DENTISTS ONLY (see NICHE.md). CAMPAIGN_NICHE should be "dentist".
# The med-spa niche was retired 2026-07-22 — do not target med-spa/cosmetic businesses.
CAMPAIGN_LOCATION = get("CAMPAIGN_LOCATION")
CAMPAIGN_NICHE = get("CAMPAIGN_NICHE")
CAMPAIGN_TARGET_COUNT = get_int("CAMPAIGN_TARGET_COUNT", 50)
# Optional multi-area sweep (semicolon-separated, e.g.
# "Plano, TX; Frisco, TX; Arlington, TX"). Legacy Places pagination is dead, so
# breadth comes from searching several areas x niche-term variants. Falls back to
# CAMPAIGN_LOCATION when unset.
CAMPAIGN_AREAS = [a.strip() for a in (get("CAMPAIGN_AREAS", "") or "").split(";")
                  if a.strip()]
# Qualification breadth. The original gate only accepted NO_SITE (no real web
# presence) and OUTDATED (visibly dated). That rejected 86% of every batch as
# "site looks modern" and — because emailability tracks having-a-site-to-scrape
# (62% of OUTDATED leads yielded an email vs 6% of NO_SITE) — starved the funnel
# to ~2 sendable leads/week. With this on, a technically-clean but weakly-
# CONVERTING site qualifies as the third tier WEAK, pitched on lost bookings
# rather than on looking dated. Set false to restore the dated-only gate.
QUALIFY_ACCEPT_WEAK = get_bool("QUALIFY_ACCEPT_WEAK", True)

# --- models ---
# Three tiers, swapped as a set by LLM_PROVIDER so an A/B flip stays one env var:
#   QUALITY    — sample HTML generation + pairwise vision judging (design taste)
#   VISION     — photo ranking (needs image input; kept off the cheap tier because
#                value-tier vision support isn't guaranteed)
#   CLASSIFIER — pure-text grunt work: qualify, reply-classify, fact-check, site-facts
# Overridable individually via env (MODEL_QUALITY / MODEL_VISION / MODEL_CLASSIFIER).
if LLM_PROVIDER == "anthropic":
    MODEL_QUALITY = get("MODEL_QUALITY", "claude-opus-4-8")
    MODEL_VISION = get("MODEL_VISION", "claude-haiku-4-5-20251001")
    MODEL_CLASSIFIER = get("MODEL_CLASSIFIER", "claude-haiku-4-5-20251001")
else:  # moonshot (Kimi)
    MODEL_QUALITY = get("MODEL_QUALITY", "kimi-k3")        # design the samples (Austin's call)
    MODEL_VISION = get("MODEL_VISION", "kimi-k3")          # native vision, picks the winner
    MODEL_CLASSIFIER = get("MODEL_CLASSIFIER", "kimi-k2.6")  # cheap text jobs

# --- sample generation: best-of-N (turn generation variance into a selection asset) ---
# For each lead, generate this many diverse candidate samples (one per design
# language) and ship the one a pairwise vision tournament judges best. 1 = single-
# shot (DEFAULT): the operator's taste now steers GENERATION directly (see taste.py /
# taste_profile.md), so we no longer pay to generate N and vision-judge C(N,2) pairs
# to pick one — generate one aimed at the taste and ship it. Set >1 to re-enable the
# tournament (REQUIRES Playwright; auto-falls back to single-shot without it).
BEST_OF_N = get_int("BEST_OF_N", 1)

# --- budgets (doc 08) ---
PIPELINE_MONTHLY_BUDGET_USD = get_float("PIPELINE_MONTHLY_BUDGET_USD", 150)
CLAUDE_MONTHLY_BUDGET_USD = get_float("CLAUDE_MONTHLY_BUDGET_USD", 50)
PLACES_MONTHLY_BUDGET_USD = get_float("PLACES_MONTHLY_BUDGET_USD", 20)
ZEROBOUNCE_MONTHLY_BUDGET_USD = get_float("ZEROBOUNCE_MONTHLY_BUDGET_USD", 10)
MAIL_MONTHLY_BUDGET_USD = get_float("MAIL_MONTHLY_BUDGET_USD", 0)
BUDGET_WARN_PCT = get_float("BUDGET_WARN_PCT", 80)
