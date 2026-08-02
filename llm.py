"""Provider-neutral LLM client.

Every Claude/Kimi call in the pipeline goes through `llm.create(...)`, which speaks
the SAME Anthropic-shaped request/response the code already used — so switching
providers is a config flip, not a rewrite of the call sites.

Providers (config.LLM_PROVIDER):
  - "moonshot" (default): Kimi via Moonshot's OpenAI-compatible endpoint.
  - "anthropic": Claude (the flip-back for A/B-ing sample quality). Its native SDK
    response already matches our interface, so that path just passes through.

The Moonshot path centralizes the two things that differ from Anthropic:
  1. request shape — system-as-message, `image_url` blocks, no cache_control;
  2. images — Moonshot rejects public image URLs, so any `{"type":"url"}` image
     block is downloaded and re-sent inline as a base64 data URI here, once, for
     every call site (the vision QC, the pairwise judge, etc.) automatically.

Normalized response exposes exactly what the call sites read:
  resp.content[0].text
  resp.usage.input_tokens / output_tokens
  resp.usage.cache_read_input_tokens / cache_creation_input_tokens  (default 0)
  resp.stop_reason  ("max_tokens" when the model hit the length cap)
"""
import base64
import logging

import requests

import config

log = logging.getLogger(__name__)

_client = None  # cached provider SDK client

# Kimi models are REASONING models: they emit hidden reasoning_content before the
# answer, and those tokens count against max_tokens. Every call site here tuned its
# max_tokens for non-reasoning Claude (a 200-token judge, a 300-token classifier),
# so on Kimi the reasoning would eat the whole budget and truncate the answer to ''
# (breaking JSON parsing — and fact_check fails OPEN). We add headroom centrally so
# each call keeps its intended ANSWER budget after reasoning, without re-tuning every
# site.
#
# 1500 was NOT enough and was silently corrupting the qualifier. Measured 2026-08-02
# on real 18k-char dental homepages: reasoning alone ran 1526, 1549, and 1750+ tokens
# — the last hit the cap and returned an EMPTY answer. In ai_site_verdict an empty
# answer parses as "not dated", i.e. `SKIP: site looks modern`, so an unknown share
# of the 86% rejection rate was truncation, not judgment. Raising the cap is close to
# free: reasoning stops when the model is done, so this only prevents truncation, it
# doesn't buy more tokens on calls that never needed them.
_REASONING_HEADROOM = 3000


def available() -> bool:
    """Is the active provider usable (key present)? Call-site guards use this."""
    return bool(config.LLM_API_KEY)


# ---------------------------------------------------------------- normalized resp
class _Text:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _Usage:
    __slots__ = ("input_tokens", "output_tokens",
                 "cache_read_input_tokens", "cache_creation_input_tokens")

    def __init__(self, input_tokens, output_tokens,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _Resp:
    __slots__ = ("content", "usage", "stop_reason")

    def __init__(self, text, usage, stop_reason):
        self.content = [_Text(text)]
        self.usage = usage
        self.stop_reason = stop_reason


# ------------------------------------------------------------------------ clients
def _moonshot_client():
    global _client
    if _client is None:
        from openai import OpenAI  # lazy: only when actually calling
        _client = OpenAI(api_key=config.MOONSHOT_API_KEY,
                         base_url=config.MOONSHOT_BASE_URL)
    return _client


def _anthropic_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ----------------------------------------------------- Anthropic -> OpenAI shapes
def _system_text(system) -> str:
    """Anthropic allows system as a string OR a list of text blocks (with
    cache_control). OpenAI wants one string in a system message."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))


def _image_data_uri(source: dict) -> str | None:
    """Anthropic image `source` block -> a base64 data URI (Moonshot rejects public
    image URLs, so URL sources are fetched and inlined here)."""
    if source.get("type") == "base64":
        return f"data:{source.get('media_type', 'image/jpeg')};base64,{source['data']}"
    if source.get("type") == "url":
        url = source.get("url", "")
        try:
            r = requests.get(url.replace("&amp;", "&"), timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and r.content:
                media = (r.headers.get("Content-Type", "").split(";")[0].strip()
                         or "image/jpeg")
                return f"data:{media};base64,{base64.b64encode(r.content).decode()}"
            log.warning("llm: image fetch %s -> HTTP %s", url[:70], r.status_code)
        except requests.RequestException:
            log.warning("llm: image fetch failed %s", url[:70])
    return None


def _openai_content(content):
    """Translate an Anthropic message `content` (str or block list) to OpenAI form.
    Drops cache_control; converts image blocks to base64 `image_url` blocks; skips
    any image that can't be fetched (one bad URL must not sink the whole call)."""
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            out.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            uri = _image_data_uri(block.get("source", {}))
            if uri:
                out.append({"type": "image_url", "image_url": {"url": uri}})
    return out


def _moonshot_create(*, model, max_tokens, system, messages, reasoning_effort=None):
    client = _moonshot_client()
    oai_messages = []
    sys_text = _system_text(system)
    if sys_text:
        oai_messages.append({"role": "system", "content": sys_text})
    for m in messages:
        oai_messages.append({"role": m.get("role", "user"),
                             "content": _openai_content(m.get("content", ""))})
    kwargs = {"model": model, "max_tokens": max_tokens + _REASONING_HEADROOM,
              "messages": oai_messages}
    if reasoning_effort is not None:  # k3 honors this; k2.x is erratic — leave unset
        kwargs["reasoning_effort"] = reasoning_effort
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    text = choice.message.content or ""
    stop = "max_tokens" if choice.finish_reason == "length" else choice.finish_reason
    u = resp.usage
    prompt = getattr(u, "prompt_tokens", 0) or 0
    completion = getattr(u, "completion_tokens", 0) or 0
    # OpenAI-compat exposes cache hits under prompt_tokens_details.cached_tokens;
    # bill those as cache_read (0.1x input) and the rest at full input rate.
    details = getattr(u, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    return _Resp(text,
                 _Usage(input_tokens=max(prompt - cached, 0), output_tokens=completion,
                        cache_read_input_tokens=cached),
                 stop)


# ------------------------------------------------------------------------ public
def create(*, model, max_tokens, messages, system=None, reasoning_effort=None):
    """Anthropic-shaped chat call, routed to the active provider. `system` is a
    string or Anthropic text-block list (both accepted). `messages` is the
    Anthropic form: [{"role": "user", "content": <str | block list>}].
    `reasoning_effort` (Moonshot/Kimi only) is passed through when set — k3 honors
    it to trim reasoning; ignored on the Anthropic path."""
    if config.LLM_PROVIDER == "anthropic":
        client = _anthropic_client()
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system is not None:
            kwargs["system"] = system
        return client.messages.create(**kwargs)  # native resp matches our interface
    return _moonshot_create(model=model, max_tokens=max_tokens, system=system,
                            messages=messages, reasoning_effort=reasoning_effort)
