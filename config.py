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


# --- core ---
ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
GOOGLE_PLACES_API_KEY = get("GOOGLE_PLACES_API_KEY")
ZEROBOUNCE_API_KEY = get("ZEROBOUNCE_API_KEY")  # optional; MX fallback if blank
# optional free stock-photo APIs (image ladder step 3; both free tiers)
PEXELS_API_KEY = get("PEXELS_API_KEY")
UNSPLASH_ACCESS_KEY = get("UNSPLASH_ACCESS_KEY")
DB_PATH = str((_HERE / get("DB_PATH", "leads.db")).resolve()) if not os.path.isabs(get("DB_PATH", "leads.db")) else get("DB_PATH")

# --- samples / tunnel ---
SAMPLES_PORT = get_int("SAMPLES_PORT", 8788)
SAMPLE_BASE_URL = (get("SAMPLE_BASE_URL") or "").rstrip("/")
SAMPLES_CACHE_DIR = _HERE / "samples_cache"
LOGS_DIR = _HERE / "logs"
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

# --- standing campaign ---
CAMPAIGN_LOCATION = get("CAMPAIGN_LOCATION")
CAMPAIGN_NICHE = get("CAMPAIGN_NICHE")
CAMPAIGN_TARGET_COUNT = get_int("CAMPAIGN_TARGET_COUNT", 50)
# Optional multi-area sweep (semicolon-separated, e.g.
# "Plano, TX; Frisco, TX; Arlington, TX"). Legacy Places pagination is dead, so
# breadth comes from searching several areas x niche-term variants. Falls back to
# CAMPAIGN_LOCATION when unset.
CAMPAIGN_AREAS = [a.strip() for a in (get("CAMPAIGN_AREAS", "") or "").split(";")
                  if a.strip()]

# --- models (doc 00: decided, do not re-litigate) ---
MODEL_QUALITY = "claude-opus-4-8"          # sample HTML + email copy
MODEL_CLASSIFIER = "claude-haiku-4-5-20251001"  # reply classification

# --- budgets (doc 08) ---
PIPELINE_MONTHLY_BUDGET_USD = get_float("PIPELINE_MONTHLY_BUDGET_USD", 150)
CLAUDE_MONTHLY_BUDGET_USD = get_float("CLAUDE_MONTHLY_BUDGET_USD", 50)
PLACES_MONTHLY_BUDGET_USD = get_float("PLACES_MONTHLY_BUDGET_USD", 20)
ZEROBOUNCE_MONTHLY_BUDGET_USD = get_float("ZEROBOUNCE_MONTHLY_BUDGET_USD", 10)
MAIL_MONTHLY_BUDGET_USD = get_float("MAIL_MONTHLY_BUDGET_USD", 0)
BUDGET_WARN_PCT = get_float("BUDGET_WARN_PCT", 80)
