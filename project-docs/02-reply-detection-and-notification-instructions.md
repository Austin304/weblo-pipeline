# 02 — Reply Detection & Phone Notification (Stage 4)

## Objective
Continuously watch the inbox for replies to outreach, decide what each reply means, and **push a phone notification to the operator (Austin) via Telegram** when a business is interested. This is the moment the system hands a warm lead to a human. It also lets the operator authorize the full build right from their phone.

Runs as a long-lived daemon (`watch_replies.py`) on the always-on GCP VM, kept alive by `systemd` (auto-restart on crash; see doc 04).

---

## STEP 1 — Detect replies (Gmail API)

Use the same authenticated Gmail API client as the send side (`email-agent-instructions.md`).

- Poll for new messages every ~60–120 seconds (or use Gmail push/`watch` if set up).
- A message is a **reply to outreach** if its `threadId` matches a `gmail_thread_id` in the `leads` table, or its `In-Reply-To`/`References` header maps to one of our sent `gmail_message_id`s.
- Pull the sender, the plain-text body, and the matching lead.
- **Reading replies is also good for sender reputation** — an account that sends and never reads looks like a bot (per email-agent-instructions.md). This daemon doubles as that signal.

---

## STEP 2 — Classify the reply (Claude)

Send the reply text to Claude (`claude-haiku-4-5-20251001` or `claude-sonnet-4-6` — cheap/fast is fine) and classify into exactly one bucket:

| Class | Meaning | Action |
|---|---|---|
| `INTERESTED` | Wants more, asks about price/next steps, positive | → `REPLIED_INTERESTED`, **notify operator** |
| `QUESTION` | Engaged but asking something before deciding | → `REPLIED_INTERESTED`, **notify operator** (operator answers) |
| `NOT_INTERESTED` | Polite no / not now | → `CLOSED_LOST`, no notification (or a quiet daily digest) |
| `OPT_OUT` | "Stop", "unsubscribe", "remove me", hostile | → `OPTED_OUT`, add email to `suppressed`, **never email again** |
| `AUTO_REPLY` | Out-of-office, autoresponder, bounce notice | ignore; leave status `EMAILED` |

Classification prompt should return strict JSON: `{"class": "...", "summary": "one line", "suggested_reply_hint": "..."}`.

**Opt-outs are mandatory to honor** (CAN-SPAM). On `OPT_OUT`, suppress immediately and confirm nothing further is ever sent to that address.

Once a lead is `REPLIED_INTERESTED`/`HANDED_OFF`, **the automated email jobs must skip it** — it's now a human conversation.

---

## STEP 3 — Notify the operator on the phone (Telegram)

### One-time setup
1. In Telegram, message **@BotFather** → `/newbot` → get the **bot token** → put in `TELEGRAM_BOT_TOKEN`.
2. Start a chat with the new bot (send it any message), then read `getUpdates` to find your **chat id** → put in `TELEGRAM_CHAT_ID`.

### Sending a notification
On `INTERESTED`/`QUESTION`, POST to the Telegram Bot API `sendMessage`:

```
https://api.telegram.org/bot<TOKEN>/sendMessage
  chat_id = <CHAT_ID>
  text    = <the alert>
  parse_mode = Markdown
```

The alert should contain everything the operator needs to act without opening a laptop:
```
🔔 Interested lead — Joe's Plumbing (Dallas, TX)

They said:
"Looks great, what would the full site cost?"

Sample: https://samples.yourstudio.com/joes-plumbing-dallas
Lead #: 142   |   Their email: joe@joesplumbing.com

Reply /handoff 142 when you've taken over,
or /build 142 to authorize the full build.
```

Set `notified_at` and move the lead to `HANDED_OFF` once notified (or keep `REPLIED_INTERESTED` until the operator acknowledges — agent's choice, just be consistent).

---

## STEP 4 — Two-way control (operator commands from the phone)

The daemon also **listens** for the operator's messages (`getUpdates` long-poll or webhook) and accepts simple commands — this is the explicit human gate the Site Goal requires:

| Command | Effect |
|---|---|
| `/handoff <lead_id>` | Marks the lead `HANDED_OFF`; confirms automation will no longer email it. |
| `/build <lead_id>` | The explicit authorization. Sets `authorized_at` + status `AUTHORIZED`, which triggers the full-build stage (doc 03). Bot replies to confirm. |
| `/find <niche> in <location> [count]` | Kicks off an **ad-hoc** lead-gen campaign for a one-off area/niche (on top of the standing weekly auto-top-up — doc 04). Bot confirms what it's searching and reports counts when done. See find-leads-brief.md. |
| `/status` | Bot replies with a quick funnel summary (counts per status today, including `PHONE_ONLY` leads awaiting a manual call). |
| `/views` | Bot replies with recent **sample visits** from the `sample_visits` table — which leads viewed their sample, the channel (postcard/email/direct), visit count, and last-viewed time. This is how the operator checks email-lead interest (email visits don't push a notification — see below). Optional `/views <lead_id>` for one lead's full visit history. |
| `/stop <lead_id>` | Manually suppress a lead/address. |

**Important:** the agent must never start a full build on its own. `AUTHORIZED` is only ever set by an explicit `/build` command (or equivalent operator action). Only commands from the configured `TELEGRAM_CHAT_ID` are honored.

### A note on sample-visit signals
Sample visits are logged by `serve_samples.py` for **every** lead (doc 01/04). They are handled differently by channel, on purpose:
- **Postcard leads** push a Telegram notification on their first visit — a visit is their only possible interest signal (no inbox to reply from). Doc 06, Step F.
- **Email leads** do **not** push a notification on a visit (their interest signal is an actual reply, handled above) — visits are logged silently and pulled up on demand with `/views`. This keeps the alerts meaningful: a push always means "act now," while `/views` answers "who's been looking?"

---

## Quality bar
- No interested reply ever goes unnoticed — every `INTERESTED`/`QUESTION` produces a phone notification within minutes.
- Opt-outs are honored immediately and permanently.
- The operator can run the entire handoff → authorize flow from their phone.
- The daemon is resilient: on crash it restarts and resumes from last-seen message id; on Gmail/Telegram API errors it retries with backoff.
