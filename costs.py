"""Spend ledger + budget gate (doc 08). Every paid call checks before spending."""
import logging
from datetime import datetime, timezone

import config
import db

log = logging.getLogger(__name__)

# per-1M-token (input, output) prices. Confirm against each provider's live pricing
# before launch — these change. Keyed by model id; the ledger stores the id, so a
# provider switch just adds rows here (claude_cost falls back to the Opus rate if a
# model id is missing, so an unknown model over-estimates rather than under-bills).
CLAUDE_PRICES = {
    # Anthropic (flip-back / legacy rows)
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    # Moonshot / Kimi (active provider)
    "kimi-k3": (3.00, 15.00),
    "kimi-k2.7-code": (0.95, 4.00),
    "kimi-k2.6": (0.95, 4.00),
    "kimi-k2.5": (0.60, 3.00),
}
PLACES_TEXT_SEARCH_USD = 0.032   # per text search request
PLACES_DETAILS_USD = 0.025       # per details request (basic + contact + atmosphere)
PLACES_PHOTO_USD = 0.007         # per photo media request
ZEROBOUNCE_CHECK_USD = 0.004

_BUDGETS = {
    "claude": config.CLAUDE_MONTHLY_BUDGET_USD,
    "places": config.PLACES_MONTHLY_BUDGET_USD,
    "zerobounce": config.ZEROBOUNCE_MONTHLY_BUDGET_USD,
    "lob": config.MAIL_MONTHLY_BUDGET_USD,
}


def claude_cost(model: str, tokens_in: int, tokens_out: int,
                cache_write: int = 0, cache_read: int = 0) -> float:
    """Cache-aware: writes bill at 1.25x input rate, reads at 0.1x."""
    in_rate, out_rate = CLAUDE_PRICES.get(model, (5.00, 25.00))
    return ((tokens_in + cache_write * 1.25 + cache_read * 0.10) / 1e6 * in_rate
            + tokens_out / 1e6 * out_rate)


def month_to_date(conn, service: str | None = None) -> float:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if service:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM spend_ledger"
            " WHERE service = ? AND created_at LIKE ?", (service, f"{month}%"),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM spend_ledger"
            " WHERE created_at LIKE ?", (f"{month}%",),
        ).fetchone()
    return row[0] or 0.0


def budget_for(service: str) -> float:
    return _BUDGETS.get(service, 0.0)


def _alert_once(conn, key: str, message: str):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    full_key = f"{key}:{month}"
    already = conn.execute(
        "SELECT 1 FROM alerts_sent WHERE key = ?", (full_key,)
    ).fetchone()
    if already:
        return
    conn.execute(
        "INSERT INTO alerts_sent (key, created_at) VALUES (?,?)",
        (full_key, db.now()),
    )
    conn.commit()
    try:
        import notify
        notify.send(message)
    except Exception:
        log.exception("budget alert notify failed")


def check(conn, service: str, est_usd: float) -> str:
    """Gate before any paid call: returns 'ok' | 'warn' | 'block'."""
    global_spent = month_to_date(conn)
    if global_spent + est_usd > config.PIPELINE_MONTHLY_BUDGET_USD:
        _alert_once(conn, "global",
                    f"⚠️ Global pipeline budget reached "
                    f"(${config.PIPELINE_MONTHLY_BUDGET_USD:.0f}/mo). Jobs paused "
                    f"until next month or until you raise PIPELINE_MONTHLY_BUDGET_USD.")
        return "block"
    service_spent = month_to_date(conn, service)
    budget = budget_for(service)
    if budget and service_spent + est_usd > budget:
        _alert_once(conn, service,
                    f"⚠️ {service} budget reached (${budget:.0f}/mo). "
                    f"That job is paused until next month or a raised cap.")
        return "block"
    if budget and service_spent + est_usd > budget * config.BUDGET_WARN_PCT / 100:
        _alert_once(conn, f"{service}-warn",
                    f"{service} spend at {service_spent + est_usd:.2f} of "
                    f"${budget:.0f}/mo ({config.BUDGET_WARN_PCT:.0f}% warning).")
        return "warn"
    return "ok"


def record(conn, service: str, operation: str, cost_usd: float,
           lead_id: int | None = None, tokens_in: int | None = None,
           tokens_out: int | None = None):
    conn.execute(
        "INSERT INTO spend_ledger (service, operation, cost_usd, lead_id,"
        " tokens_in, tokens_out, created_at) VALUES (?,?,?,?,?,?,?)",
        (service, operation, cost_usd, lead_id, tokens_in, tokens_out, db.now()),
    )
