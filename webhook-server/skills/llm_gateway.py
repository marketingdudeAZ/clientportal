"""LLM Gateway — the single door to Claude.

CLAUDE.md Key Rule 2: "All Claude API calls go through the LLM Gateway, not
direct SDK calls." The gateway was never built, so today 20 modules construct
their own `anthropic.Anthropic(...)` and two bypass the SDK entirely with hand-
rolled HTTPS POSTs (`kb_writer.py:158`, `services/fluency_ingestion/
url_scraper.py:37`). There is no retry policy, no cost accounting, no shared
timeout, and no one place to change a model id.

That last one matters right now: the three model constants in `config.py` have
drifted out of date.

    CLAUDE_BRIEF_MODEL  = "claude-haiku-4-5-20251001"   date-suffixed
    CLAUDE_DIGEST_MODEL = "claude-sonnet-4-5"           not a current model
    CLAUDE_AGENT_MODEL  = "claude-sonnet-4-5"           not a current model

Model ids are complete as-is and must not carry date suffixes. `MODEL_ALIASES`
below maps the legacy names forward so the 20 existing call sites keep working
while they migrate, and `resolve_model()` logs once per stale name so the drift
is visible rather than silent.

Adoption is a strangler, the pattern `hubspot_client.py` established: new code
imports this; existing call sites move one at a time. Migrating all 20 at once
is the shape that caused the "every webhook 401'd" outage.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Default for anything that does not name a model. Never downgrade to a cheaper
# tier on a caller's behalf — that is a product decision, not a plumbing one.
DEFAULT_MODEL = "claude-opus-5"

# Current ids. No date suffixes.
MODELS = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# Legacy config constants -> current ids, so existing callers keep working.
MODEL_ALIASES = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-sonnet-5",
    "claude-3-5-sonnet-20241022": "claude-sonnet-5",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
}

# Per 1M tokens, for cost accounting only.
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_DEFAULT_MAX_TOKENS = 16_000          # non-streaming; stays under HTTP timeouts
_STREAM_MAX_TOKENS = 64_000
_TIMEOUT = float(os.environ.get("LLM_GATEWAY_TIMEOUT", "180"))
_MAX_RETRIES = int(os.environ.get("LLM_GATEWAY_MAX_RETRIES", "2"))

_client = None
_client_lock = threading.Lock()
_warned_aliases: set[str] = set()


class LLMError(RuntimeError):
    """A Claude call that failed and should not be silently swallowed."""


class LLMNotConfigured(LLMError):
    """No API key. Distinct from a call that failed — absence is not failure."""


class LLMRefusal(LLMError):
    """The model declined. Carries the category so callers can branch."""

    def __init__(self, message: str, category: str | None = None):
        super().__init__(message)
        self.category = category


@dataclass
class Response:
    """What every caller gets back. `text` is the common case; the rest is there
    when someone needs to account for spend or inspect reasoning."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str | None = None
    thinking: str | None = None
    raw: Any = field(default=None, repr=False)

    @property
    def cost_usd(self) -> float:
        rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
        billable_in = self.input_tokens + int(self.cache_read_tokens * 0.1)
        return (billable_in * rate_in + self.output_tokens * rate_out) / 1_000_000

    def __str__(self) -> str:          # so `str(resp)` behaves like the old code
        return self.text


def resolve_model(model: str | None) -> str:
    """Map a shorthand or a legacy id to a current model id."""
    if not model:
        return DEFAULT_MODEL
    if model in MODELS:
        return MODELS[model]
    if model in MODEL_ALIASES:
        current = MODEL_ALIASES[model]
        if model not in _warned_aliases:
            _warned_aliases.add(model)
            logger.warning(
                "llm_gateway: model %r is out of date, using %r. Update the "
                "constant in config.py.", model, current,
            )
        return current
    return model


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if not is_configured():
            raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
        import anthropic
        _client = anthropic.Anthropic(timeout=_TIMEOUT, max_retries=_MAX_RETRIES)
        return _client


def _emit_usage(resp: Response, purpose: str, elapsed_ms: int) -> None:
    """Record spend on the Loop event bus.

    ADR 0010 put observability in `loop_events` rather than a separate jobs
    table, so token spend belongs there too — stage 'ops'. Best-effort: a
    metrics write must never break the caller's request.
    """
    try:
        import loop_writer
        loop_writer.record(
            stage="ops",
            event_type="llm_call_completed",
            source="llm_gateway",
            trigger=purpose,
            status="success",
            runtime_ms=elapsed_ms,
            payload={
                "model": resp.model,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "cache_read_tokens": resp.cache_read_tokens,
                "cost_usd": round(resp.cost_usd, 6),
                "purpose": purpose,
            },
        )
    except Exception as exc:                                   # noqa: BLE001
        logger.debug("llm_gateway: usage event not recorded: %s", exc)


def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    thinking: bool = False,
    effort: str | None = None,
    cache_system: bool = False,
    stream: bool | None = None,
    purpose: str = "unspecified",
    messages: list[dict] | None = None,
) -> Response:
    """One Claude call.

    `thinking` turns on adaptive thinking — use it for anything that reasons
    over data rather than reformatting it. `effort` ("low".."max") tunes depth.
    `cache_system` caches the system prompt, which pays off when the same large
    context is reused across calls (the SWOT and digest paths both do this).

    Streaming is chosen automatically when max_tokens is large enough that a
    non-streaming call would risk an HTTP timeout.

    Raises LLMNotConfigured when there is no key, LLMRefusal when the model
    declines, LLMError otherwise. It does not return an empty string on
    failure — `hyly_client` returning [] on error is why a broken integration
    read as "no data" for weeks.
    """
    resolved = resolve_model(model)
    if max_tokens is None:
        max_tokens = _STREAM_MAX_TOKENS if stream else _DEFAULT_MAX_TOKENS
    if stream is None:
        stream = max_tokens > _DEFAULT_MAX_TOKENS

    client = _get_client()

    kwargs: dict[str, Any] = {
        "model": resolved,
        "max_tokens": max_tokens,
        "messages": messages or [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if cache_system else system
        )
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    if effort:
        kwargs["output_config"] = {"effort": effort}

    started = time.time()
    try:
        if stream:
            with client.messages.stream(**kwargs) as s:
                msg = s.get_final_message()
        else:
            msg = client.messages.create(**kwargs)
    except Exception as exc:                                    # noqa: BLE001
        import anthropic
        if isinstance(exc, anthropic.APIStatusError):
            raise LLMError(f"Claude {exc.status_code}: {exc.message}") from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise LLMError(f"Claude unreachable: {exc}") from exc
        raise LLMError(str(exc)) from exc

    elapsed_ms = int((time.time() - started) * 1000)

    if getattr(msg, "stop_reason", None) == "refusal":
        details = getattr(msg, "stop_details", None)
        raise LLMRefusal(
            f"model declined ({getattr(details, 'category', None)})",
            category=getattr(details, "category", None),
        )

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    think = "".join(
        getattr(b, "thinking", "") for b in msg.content
        if getattr(b, "type", None) == "thinking"
    ) or None

    usage = getattr(msg, "usage", None)
    resp = Response(
        text=text,
        model=resolved,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        stop_reason=getattr(msg, "stop_reason", None),
        thinking=think,
        raw=msg,
    )
    # Belt and braces: _emit_usage guards itself, but a metrics path must not be
    # able to fail a call that already succeeded and cost money.
    try:
        _emit_usage(resp, purpose, elapsed_ms)
    except Exception as exc:                                    # noqa: BLE001
        logger.debug("llm_gateway: usage accounting failed: %s", exc)
    logger.info(
        "llm_gateway: %s model=%s in=%d out=%d cost=$%.4f %dms",
        purpose, resolved, resp.input_tokens, resp.output_tokens,
        resp.cost_usd, elapsed_ms,
    )
    return resp


def count_tokens(prompt: str, *, system: str | None = None,
                 model: str | None = None) -> int:
    """Token count before sending. Never estimate with a third-party tokenizer."""
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": resolve_model(model),
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    return client.messages.count_tokens(**kwargs).input_tokens
