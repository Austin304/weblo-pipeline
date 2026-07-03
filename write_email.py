"""Email copy via Claude (email-writing-instructions.md)."""
import json
import logging
import re

import config
import costs
import db

log = logging.getLogger(__name__)

EST_EMAIL_USD = 0.05
ARCHETYPES = [
    "Observation -> pivot: note the specific detail, then pivot to 'it got me thinking your website should show that off'",
    "Honest question: ask something real about how customers currently find them online",
    "Quick compliment -> gap: praise something specific, then gently note their site doesn't match how good they actually are",
    "Direct + casual: 'I'll be quick —' then straight to the point",
]

BANNED = [
    "i hope this email finds you well", "revolutionize", "unlock", "elevate",
    "synergy", "game-changer", "cutting-edge", "leverage", "dear sir",
    "to whom it may concern", "act now", "limited time", "100% free",
]

SYSTEM = "You write short, human cold emails for a freelance local web designer. You sound like one real person emailing another — never like marketing."


def _call(conn, prompt: str, lead_id: int, operation: str) -> dict | None:
    if costs.check(conn, "claude", EST_EMAIL_USD) == "block":
        return None
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.MODEL_QUALITY, max_tokens=1000, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    costs.record(conn, "claude", operation,
                 costs.claude_cost(config.MODEL_QUALITY,
                                   resp.usage.input_tokens,
                                   resp.usage.output_tokens),
                 lead_id=lead_id, tokens_in=resp.usage.input_tokens,
                 tokens_out=resp.usage.output_tokens)
    conn.commit()
    raw = resp.content[0].text
    try:
        return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        log.warning("email copy JSON parse failed for lead %s", lead_id)
        return None


def _lint(subject: str, body: str, sample_url: str, max_words: int) -> str | None:
    text = f"{subject} {body}".lower()
    for phrase in BANNED:
        if phrase in text:
            return f"banned phrase: {phrase}"
    links = re.findall(r"https?://\S+", body)
    if len(links) != 1 or sample_url not in links[0]:
        return "must contain exactly one link: the sample URL"
    words = len(body.split())
    if words > max_words:
        return f"too long ({words} words)"
    if "!" in subject or subject.isupper():
        return "subject has punctuation gimmicks / caps"
    return None


def _profile_context(lead) -> str:
    profile = json.loads(lead["source_profile"] or "{}")
    reviews = "\n".join(f"- \"{r['text'][:200]}\"" for r in (profile.get("reviews") or [])[:3])
    return (
        f"Business: {lead['business_name']} ({lead['category']})\n"
        f"Address: {lead['address']}\n"
        f"Rating: {lead['rating']} from {lead['review_count']} reviews\n"
        f"Their current site problem: {lead['qualify_reason'] or 'no website at all'}\n"
        f"Real review quotes:\n{reviews or '- (none pulled)'}\n"
        f"About: {profile.get('summary') or '(none)'}"
    )


def write_cold_email(conn, lead) -> tuple[str, str] | None:
    archetype = ARCHETYPES[lead["id"] % len(ARCHETYPES)]
    for attempt in range(2):
        prompt = f"""Write ONE cold outreach email to this local business. I built them a free sample website; the email's only job is to get them to look at it.

HARD RULES:
- 40-110 words. Shorter beats longer. Plain text, one real person to another.
- EXACTLY ONE link, used as the call to action: {lead['sample_url']}  (use it exactly, no shorteners)
- One ask only: look at the sample. Never ask for a call or meeting.
- Open with ONE specific, TRUE observation about this business from the facts below (a review quote, how long established, a service, the location). Never generic.
- Opening archetype to use: {archetype}
- Subject: 2-5 words, sentence case, no punctuation gimmicks, no emojis.
- Banned: {', '.join(BANNED[:8])}, ALL CAPS, multiple exclamation points.
- Do NOT include a signature block, opt-out line, or address — those are appended separately. End after the soft close with just my first name: {(config.SENDER_NAME or 'Austin').split()[0]}

THE FACTS (use only these; invent nothing):
{_profile_context(lead)}

Return STRICT JSON only: {{"subject": "...", "body": "..."}}"""
        data = _call(conn, prompt, lead["id"], "cold_email")
        if not data:
            return None
        subject, body = data.get("subject", "").strip(), data.get("body", "").strip()
        problem = _lint(subject, body, lead["sample_url"], max_words=115)
        if problem is None:
            return subject, body
        log.info("lead %s email lint fail (%s); retrying", lead["id"], problem)
    return None


def write_followup(conn, lead) -> tuple[str, str] | None:
    prompt = f"""Write the ONE follow-up email to a business that didn't reply to the first email below. It's a light nudge, not a re-pitch.

RULES:
- 25-60 words, shorter than the first. Genuinely fresh wording — never a resend.
- Reference the same true detail but frame it differently.
- Re-share the same link once: {lead['sample_url']}
- Soft, low-pressure close (assume busy, not uninterested). One ask: look at the sample.
- New short subject (2-5 words, sentence case). No signature/opt-out/address; end with just: {(config.SENDER_NAME or 'Austin').split()[0]}

THE FIRST EMAIL THEY DIDN'T ANSWER:
Subject: {lead['email_subject']}
{lead['email_body']}

THE FACTS:
{_profile_context(lead)}

Return STRICT JSON only: {{"subject": "...", "body": "..."}}"""
    data = _call(conn, prompt, lead["id"], "followup_email")
    if not data:
        return None
    subject, body = data.get("subject", "").strip(), data.get("body", "").strip()
    if _lint(subject, body, lead["sample_url"], max_words=70) is None:
        return subject, body
    return None
