"""One place that talks to Claude, used by the planner, caption writer and image checker.

Every call asks for an answer in a fixed JSON shape (so the agent can check
it), turns errors into plain English, and reports the cost.
"""

import json
import logging

import anthropic

from app.brands import Brand

log = logging.getLogger(__name__)
MODEL = "claude-opus-5-5"
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}  # US$


class ClaudeError(Exception):
    """A problem talking to Claude, with a message the owner can act on."""


def client_for(brand: Brand) -> anthropic.Anthropic:
    key = brand.secret("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("Claude isn't connected yet. Go to Connect accounts → Claude.")
    return anthropic.Anthropic(api_key=key, timeout=300)


def ask(client, system: str, messages: list, schema: dict, effort: str = "high",
        max_tokens: int = 16000) -> tuple[dict, float, str]:
    """Ask Claude; returns (answer as a dict, cost in US$, model that answered)."""
    response = _create(client, max_tokens=max_tokens, system=system, messages=messages,
                       output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    if response.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer came back cut off. Try again.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), cost(response.usage), response.model


def chat(client, system: str, messages: list, tools: list, effort: str = "medium", max_tokens: int = 8000):
    """One step of a conversation where Claude may use the agent's tools (used by the chat assistant).
    Returns Claude's full response; the caller runs any tools it asks for and calls again."""
    return _create(client, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                   output_config={"effort": effort})


WEB_SEARCH_USD = 0.01  # US$10 per 1,000 searches (Anthropic price list)


def research(client, system: str, prompt: str, max_searches: int = 8, effort: str = "medium",
             allowed_domains: list[str] | None = None) -> tuple[str, float, int]:
    """Let Claude search the web and write findings in plain text with sources.
    Returns (text, cost in US$, number of searches). Used by the Market Researcher."""
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches,
            "user_location": {"type": "approximate", "country": "IN", "timezone": "Asia/Kolkata"}}
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    messages = [{"role": "user", "content": prompt}]
    total, searches = 0.0, 0
    for _ in range(4):  # the search loop can pause; resume it a few times at most
        response = _create(client, system=system, max_tokens=16000, messages=messages, tools=[tool],
                           output_config={"effort": effort})
        total += cost(response.usage)
        used = getattr(getattr(response.usage, "server_tool_use", None), "web_search_requests", 0) or 0
        searches += used
        total += used * WEB_SEARCH_USD
        if response.stop_reason != "pause_turn":
            break
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": response.content}]
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise ClaudeError("The web research came back empty. Try again later.")
    return text, total, searches


def _create(client, system: str, **kwargs):
    try:
        response = client.beta.messages.create(
            model=MODEL,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if a safety check declines, Anthropic retries on a suitable model
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            **kwargs,
        )
    except anthropic.AuthenticationError:
        raise ClaudeError("Claude says the API key is wrong. Paste it again on Connect accounts → Claude.") from None
    except anthropic.PermissionDeniedError:
        raise ClaudeError("Claude refused the key — check billing/credit at console.anthropic.com.") from None
    except anthropic.RateLimitError:
        raise ClaudeError("Claude is busy (rate limit). Try again in a minute.") from None
    except anthropic.APIStatusError as e:
        if "credit" in str(e.message).lower():
            raise ClaudeError("Your Claude account is out of credit. Add credit at console.anthropic.com → Billing.") from None
        raise ClaudeError(f"Claude replied with an error ({e.status_code}). Try again shortly.") from None
    except anthropic.APIConnectionError:
        raise ClaudeError("Couldn't reach Claude. Check your internet connection.") from None
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request. Check the brand rules for anything unusual and try again.")
    return response


def cost(usage) -> float:
    def n(name):
        return getattr(usage, name, 0) or 0
    p = PRICE_PER_MTOK
    return (n("input_tokens") * p["input"] + n("output_tokens") * p["output"]
            + n("cache_creation_input_tokens") * p["cache_write"] + n("cache_read_input_tokens") * p["cache_read"]) / 1e6
