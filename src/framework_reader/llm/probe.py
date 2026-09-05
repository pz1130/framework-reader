"""The "Probe" button: takes a provider+model pair and actually sends one minimal request,
answering "can it actually be used?".

The catalog (`catalog.py`) answers "which models does this vendor offer" — that is
**category-2 egress**, carrying no content. The probe goes through the chat endpoint, the
same path as drafting — so it is **category-1 egress** and is still wrapped in
`GuardedClient`. The only difference is the payload: one fixed greeting, without a single
word of framework content. `tests/llm/test_probe.py` pins down verbatim what gets sent.

This file does not send requests itself: real requests still live in each adapter's own
`_default_*`, and the probe only assembles the client. So the egress-point list (the
whitelist in `tests/test_no_network_in_tests.py`) **does not need to change by a single
entry** — if it goes red, this design has gone off the rails.

**No retries.** `registry.build()` wraps a `RetryingClient` — that is for batch drafting,
where three backoff attempts combined can drag on for minutes. The person who clicked
"Probe" is staring at the screen waiting, and three 500s tell them the same thing as one
500. So the client is assembled here directly instead of going through `build()`.
"""
import time
from dataclasses import dataclass

from framework_reader.llm.client import Message
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.registry import ProviderPreset

# The sentence the probe asks. **It must be unrelated to any framework content**, and
# short enough that the unrelatedness is obvious at a glance.
#
# A real question rather than a bare `ping`, because everything this product writes is in
# one language: a model that cannot manage a fluent reply in it does not become usable for
# drafting just because "the endpoint is up". Letting the person **see the words it
# replied with** says more than a green checkmark.
PROBE_PROMPT = "Reply with one word: ok"

# Reply truncation. The probe only needs to prove "it spoke"; it is not a chat window.
REPLY_LIMIT = 120

# Catalog queries get 15 s, drafting 120 s. The probe sits in between: it must run one
# real inference, but the button cannot just hang there. Same trade-off as
# `catalog.TIMEOUT_SECONDS`.
TIMEOUT_SECONDS = 20.0

MAX_TOKENS = 16


@dataclass(frozen=True)
class ProbeResult:
    """`message` and `reply` are both rendered verbatim on the page. **Neither may ever contain a key.**"""

    ok: bool
    kind: str  # ok | auth | unsupported | unreachable
    message: str
    reply: str = ""
    elapsed_ms: int = 0


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _build(preset: ProviderPreset, api_key: str, http_post, send) -> GuardedClient:
    if preset.kind == "anthropic":
        from framework_reader.llm.anthropic_adapter import AnthropicClient

        inner = AnthropicClient(
            api_key, send=send, cache_system=False, timeout=TIMEOUT_SECONDS)
    else:
        from framework_reader.llm.openai_compat import OpenAICompatClient

        inner = OpenAICompatClient(
            preset.base_url, api_key, http_post=http_post, timeout=TIMEOUT_SECONDS)
    # Empty guard: the payload is the constant above and contains no source text. It is
    # wrapped anyway so that the rule "every chat egress goes through GuardedClient"
    # has no exceptions — a rule with exceptions is a rule nobody remembers.
    return GuardedClient(inner, PayloadGuard([]))


def probe_model(
    preset: ProviderPreset,
    model: str,
    api_key: str,
    *,
    http_post=None,
    send=None,
) -> ProbeResult:
    """Sends one minimal request. **Never raises** — the caller is a button, and what it
    wants is a sentence.

    `http_post` / `send` exist only for test injection; same pattern as
    `catalog.fetch_models`.
    """
    client = _build(preset, api_key, http_post, send)
    started = time.monotonic()
    try:
        reply = client.complete(
            "", [Message(role="user", content=PROBE_PROMPT)],
            model=model, max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 —— any failure must map to one of the three kinds
        elapsed = int((time.monotonic() - started) * 1000)
        return _failure(preset, model, exc, elapsed)

    elapsed = int((time.monotonic() - started) * 1000)
    return ProbeResult(
        ok=True, kind="ok",
        message=f"{preset.id} / {model} works.",
        reply=_spoken(reply)[:REPLY_LIMIT],
        elapsed_ms=elapsed,
    )


def _spoken(reply: str | None) -> str:
    """Strips the chain of thought, keeping only what the model actually said out loud.

    Reasoning models (deepseek-reasoner, MiniMax-M2.7, etc.) put their scratchpad in the
    message body. In practice MiniMax-M2.7 burned these few tokens thinking and got cut
    off halfway, so the echo became "it replied: `<think>The user is speaking Chinese…`"
    — that is not its answer.

    An unclosed `<think>` means there is no body at all after it and the whole thing is
    scratchpad: strip it to empty. The page has something to say about an empty reply
    ("it returned no words"), and that message is honest; serving up half a scratchpad
    is not.
    """
    import re

    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    return re.sub(r"<think>.*$", "", text, flags=re.S).strip()


def _failure(
    preset: ProviderPreset, model: str, exc: Exception, elapsed: int
) -> ProbeResult:
    """**Exception text never goes into message verbatim.** It may carry request details,
    and a person only needs to know which link in the chain broke."""
    status = _status_of(exc)
    if status in (401, 403):
        return ProbeResult(
            False, "auth",
            f"{preset.id} rejected this key. The key itself was untouched - "
            "either it is wrong, or this account has no access.",
            elapsed_ms=elapsed)
    if status in (400, 404):
        return ProbeResult(
            False, "unsupported",
            f"{preset.id} does not recognize {model} as a model name. Pick one from the catalog, "
            'or click "Refresh" to pull the catalog again.',
            elapsed_ms=elapsed)
    if status is None and isinstance(exc, RuntimeError):
        # RuntimeError is what an adapter raises when it cannot parse the structure. The
        # endpoint replied 200, but not in a shape we recognize.
        return ProbeResult(
            False, "unsupported",
            f"{preset.id} replied, but the structure is unrecognized - this endpoint is probably not "
            "an OpenAI-compatible /chat/completions.",
            elapsed_ms=elapsed)
    return ProbeResult(
        False, "unreachable",
        f"Could not reach {preset.id}, or it took too long to reply (waited {TIMEOUT_SECONDS:.0f} s)."
        " Try again in a bit.",
        elapsed_ms=elapsed)
