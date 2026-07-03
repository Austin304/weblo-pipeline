"""Samples web app (docs 01/04). Serves samples_cache/<slug>/index.html,
logs every visit to sample_visits, and pushes a Telegram alert on a
postcard lead's FIRST visit. Fronted publicly by the Cloudflare Tunnel.

Run as a systemd service on the VM:  python serve_samples.py
"""
import logging

from flask import Flask, abort, request, send_from_directory

import config
import db
import notify

log = logging.getLogger(__name__)
app = Flask(__name__)

SAFE_SLUG = set("abcdefghijklmnopqrstuvwxyz0123456789-")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/<slug>")
@app.get("/<slug>/")
def sample(slug: str):
    slug = slug.lower()
    if not slug or set(slug) - SAFE_SLUG:
        abort(404)
    path = config.SAMPLES_CACHE_DIR / slug / "index.html"
    if not path.is_file():
        abort(404)

    try:
        with db.connect() as conn:
            lead = db.get_lead_by_slug(conn, slug)
            if lead is not None:
                first = db.log_visit(
                    conn, lead["id"],
                    source=request.args.get("utm_source", "direct"),
                    user_agent=request.headers.get("User-Agent", "")[:300],
                    ip=request.headers.get("CF-Connecting-IP",
                                           request.remote_addr or ""),
                    referrer=request.referrer or "",
                )
                conn.commit()
                # postcard leads: a visit IS the interest signal (doc 06 Step F);
                # email leads are logged only — their signal is a reply (doc 02)
                if first and lead["email_status"] == "not_found":
                    notify.send(
                        f"👀 *Sample viewed — {lead['business_name']}*\n"
                        f"(postcard lead, first visit)\n\n"
                        f"Sample: {lead['sample_url']}\n"
                        f"Lead #: {lead['id']}   |   Phone: {lead['phone']}"
                    )
    except Exception:
        # never let tracking break the page itself
        log.exception("visit logging failed for %s", slug)

    return send_from_directory(config.SAMPLES_CACHE_DIR / slug, "index.html")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    app.run(host="127.0.0.1", port=config.SAMPLES_PORT)
