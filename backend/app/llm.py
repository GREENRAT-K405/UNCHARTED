"""Thin wrapper over an LLM with JSON-schema structured output: Groq (OpenAI-compatible API)
or the Claude Messages API.

Returns None whenever the LLM is unavailable (no credentials, LLM_MODE=offline,
network error, refusal) so callers can fall back to the rule-based agent.
"""
import asyncio
import json
import logging

import anthropic
import httpx

from .config import settings

log = logging.getLogger("pehchaan.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_client: anthropic.AsyncAnthropic | httpx.AsyncClient | None = None
status = {"mode": "offline", "model": None, "last_error": None}

_provider = settings.llm_mode
if _provider == "auto":
    _provider = "groq" if settings.groq_api_key else "claude"

if _provider == "groq":
    if settings.groq_api_key:
        _client = httpx.AsyncClient(timeout=90.0, headers={"Authorization": f"Bearer {settings.groq_api_key}"})
        status.update(mode="groq", model=settings.groq_model)
    else:
        status["last_error"] = "LLM_MODE=groq but GROQ_API_KEY is not set"
        log.warning("Groq unavailable, running memory agent in offline mode: no GROQ_API_KEY")
elif _provider == "claude":
    try:
        _client = anthropic.AsyncAnthropic(max_retries=2, timeout=90.0)
        status.update(mode="claude", model=settings.claude_model)
    except Exception as e:  # no credentials configured
        status["last_error"] = str(e)
        log.warning("Claude unavailable, running memory agent in offline mode: %s", e)


def available() -> bool:
    return _client is not None


def _go_offline(reason: str) -> None:
    global _client
    _client = None
    status.update(mode="offline", last_error=reason)


def _parse(text: str, finish: str) -> dict | None:
    try:
        status["last_error"] = None
        return json.loads(text)
    except json.JSONDecodeError:
        status["last_error"] = "invalid json"
        log.error("%s returned invalid JSON (finish=%s): %s", status["mode"], finish, text[:300])
        return None


async def structured(system: str, user: str, schema: dict, effort: str = "low", max_tokens: int = 16000) -> dict | None:
    if _client is None:
        return None
    if isinstance(_client, httpx.AsyncClient):
        return await _groq(system, user, schema, effort, max_tokens)
    return await _claude(system, user, schema, effort, max_tokens)


async def _groq(system: str, user: str, schema: dict, effort: str, max_tokens: int) -> dict | None:
    body = {
        "model": settings.groq_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        # Groq reserves max_completion_tokens against the per-minute token budget up front,
        # so a large value is rejected outright on the free tier.
        "max_completion_tokens": min(max_tokens, settings.groq_max_tokens),
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
    }
    if settings.groq_model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = effort
    try:
        for attempt in range(3):
            r = await _client.post(GROQ_URL, json=body)
            if r.status_code != 429:
                break
            # Free-tier rate limit: wait it out briefly rather than dropping to the offline agent.
            wait = float(r.headers.get("retry-after") or 10)
            status["last_error"] = f"rate limited (retry in {wait:.0f}s)"
            if wait > 30 or attempt == 2:
                log.warning("Groq rate limited, giving up after %d tries: %s", attempt + 1, r.text[:200])
                return None
            log.info("Groq rate limited, retrying in %.0fs", wait)
            await asyncio.sleep(wait)
    except httpx.HTTPError as e:
        status["last_error"] = f"connection: {e}"
        log.error("Groq connection error: %s", e)
        return None

    if r.status_code in (401, 403):
        _go_offline(f"auth: {r.text[:200]}")
        log.error("Groq auth failed, switching to offline agent: %s", r.text[:200])
        return None
    if r.status_code != 200:
        status["last_error"] = f"{r.status_code}: {r.text[:200]}"
        log.error("Groq API error %s: %s", r.status_code, r.text[:300])
        return None
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        log.warning("Groq output hit max_completion_tokens=%s; raise GROQ_MAX_TOKENS", body["max_completion_tokens"])
    return _parse(choice["message"].get("content") or "", choice.get("finish_reason"))


async def _claude(system: str, user: str, schema: dict, effort: str, max_tokens: int) -> dict | None:
    try:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            # If a safety classifier declines, let the API re-run on its recommended fallback model.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, TypeError) as e:
        # Missing (TypeError from the SDK's auth resolution) or rejected credentials:
        # switch to the offline agent instead of failing every call.
        _go_offline(f"auth: {e}")
        log.error("Claude auth failed, switching to offline agent: %s", e)
        return None
    except anthropic.RateLimitError as e:
        status["last_error"] = f"rate limited: {e}"
        log.warning("Claude rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        status["last_error"] = f"{e.status_code}: {e.message}"
        log.error("Claude API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        status["last_error"] = f"connection: {e}"
        log.error("Claude connection error: %s", e)
        return None
    except Exception as e:  # never let the LLM take the call flow down; the offline path takes over
        status["last_error"] = f"unexpected: {e}"
        log.exception("unexpected Claude client error")
        return None

    if response.stop_reason == "refusal":
        status["last_error"] = "refusal"
        log.warning("Claude declined: %s", response.stop_details)
        return None
    text = "".join(b.text for b in response.content if b.type == "text")
    return _parse(text, response.stop_reason)
