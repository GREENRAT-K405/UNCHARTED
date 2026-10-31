"""Thin wrapper over the Claude Messages API with JSON-schema structured output.

Returns None whenever Claude is unavailable (no credentials, LLM_MODE=offline,
network error, refusal) so callers can fall back to the rule-based agent.
"""
import json
import logging

import anthropic

from .config import settings

log = logging.getLogger("pehchaan.llm")

_client: anthropic.AsyncAnthropic | None = None
status = {"mode": "offline", "last_error": None}

if settings.llm_mode != "offline":
    try:
        _client = anthropic.AsyncAnthropic(max_retries=2, timeout=90.0)
        status["mode"] = "claude"
    except Exception as e:  # no credentials configured
        status["last_error"] = str(e)
        log.warning("Claude unavailable, running memory agent in offline mode: %s", e)


def available() -> bool:
    return _client is not None


async def structured(system: str, user: str, schema: dict, effort: str = "low", max_tokens: int = 16000) -> dict | None:
    global _client
    if _client is None:
        return None
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
        _client = None
        status.update(mode="offline", last_error=f"auth: {e}")
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
    try:
        status["last_error"] = None
        return json.loads(text)
    except json.JSONDecodeError:
        status["last_error"] = "invalid json"
        log.error("Claude returned invalid JSON (stop_reason=%s): %s", response.stop_reason, text[:300])
        return None
