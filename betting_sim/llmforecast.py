"""An LLM + web-search forecaster — the 'smart model' layer, done honestly.

Given a market question, this asks Claude (with live web search) to research the
current evidence and return a *calibrated* probability, using superforecaster
discipline: start from a base rate, adjust on specific evidence, and avoid
overconfidence. That estimate is then meant to be fed straight into
forecast.evaluate() against real market prices and outcomes — because a
smart-sounding rationale means nothing until it beats the market's Brier score.

Honest expectations, so this doesn't become a money-bonfire:

  * On LIQUID markets (big elections, Fed decisions) the crowd already read the
    same news. The LLM will usually just reproduce the market price. That's not
    failure — it's the market being efficient. Expect ~zero edge there.
  * The plausible edge is SPEED (react to breaking news before a slow market
    reprices), BREADTH (cover many neglected markets no pro bothers with), and
    NICHE/thin markets where the 'efficient' price is really just a few people.
  * You must still prove it: run it over many markets, track skill score and
    ROI out-of-sample. One good call is luck.

This costs API tokens, so nothing here runs unless you call it. The core sim and
its tests never touch it.
"""
from __future__ import annotations

import json
import re

PROMPT = """You are a calibrated superforecaster. Estimate the probability that \
the following market resolves YES.

Question: {question}
{context}

Method:
1. State a base rate / reference class before looking at specifics.
2. Use web search to gather the most current, relevant evidence.
3. Adjust from the base rate only as far as the evidence justifies.
4. Guard against overconfidence — extreme probabilities (>0.95, <0.05) need
   strong, specific evidence. When genuinely uncertain, stay near your base rate.

Respond with ONLY a JSON object:
{{"probability": <0..1>, "reasoning": "<2-3 sentence justification>"}}"""


def forecast(question: str, *, context: str = "",
             model: str = "claude-haiku-4-5-20251001",
             use_web_search: bool = True, max_tokens: int = 1024,
             api_key: str | None = None) -> tuple[float, str]:
    """Return (probability, reasoning) for a market question. Lazy-imports the
    anthropic SDK so the rest of the package stays dependency-light."""
    import anthropic  # lazy

    if api_key is None:
        try:
            from . import config as _cfg  # repo config (parent dir)
            api_key = getattr(_cfg, "ANTHROPIC_API_KEY", None)
        except Exception:  # noqa: BLE001
            api_key = None

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    tools = ([{"type": "web_search_20250305", "name": "web_search",
               "max_uses": 5}] if use_web_search else [])

    prompt = PROMPT.format(
        question=question,
        context=(f"Context: {context}" if context else ""))

    kwargs = dict(model=model, max_tokens=max_tokens,
                  messages=[{"role": "user", "content": prompt}])
    if tools:
        kwargs["tools"] = tools
    resp = client.messages.create(**kwargs)

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return _parse(text)


def _parse(text: str) -> tuple[float, str]:
    """Pull the probability + reasoning out of the model's JSON reply."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            p = float(obj.get("probability"))
            return max(0.0, min(1.0, p)), str(obj.get("reasoning", "")).strip()
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    # fallback: first standalone 0..1 number in the text
    m2 = re.search(r"\b(0?\.\d+|0|1)\b", text)
    if m2:
        return max(0.0, min(1.0, float(m2.group(1)))), text.strip()[:300]
    raise ValueError(f"could not parse a probability from model reply: {text[:200]}")
