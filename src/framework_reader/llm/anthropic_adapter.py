"""Native Anthropic adapter. W2 spec §3.1, §3.4①

The only vendor supporting explicit prompt caching; a cache hit or miss does not affect
correctness.
"""
import json
from collections.abc import Callable

from framework_reader.llm.client import Message

Send = Callable[[dict], dict]


def build_payload(
    system: str,
    messages: list[Message],
    model: str,
    max_tokens: int,
    cache_system: bool,
) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [m.model_dump() for m in messages],
    }
    if system.strip():
        block: dict = {"type": "text", "text": system}
        if cache_system:
            block["cache_control"] = {"type": "ephemeral"}
        payload["system"] = [block]
    return payload


def _default_send(payload: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    return client.messages.create(**payload).model_dump()


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        *,
        send: Send | None = None,
        cache_system: bool = True,
        timeout: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._cache_system = cache_system
        self._timeout = timeout
        self._send = send or self._make_default_send()

    def _make_default_send(self) -> Send:
        def send(payload: dict) -> dict:
            import anthropic

            kwargs = {"api_key": self._api_key}
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            client = anthropic.Anthropic(**kwargs)
            return client.messages.create(**payload).model_dump()

        return send

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        # Anthropic does not accept OpenAI-style response_format JSON mode. If the
        # caller turns it on there is no error — it is just dropped. JSON output is
        # guarded by the "reply with a JSON array only" instruction in the prompt.
        del response_format
        data = self._send(
            build_payload(system, messages, model, max_tokens, self._cache_system)
        )
        blocks = data.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise RuntimeError(
                f"provider returned an unexpected structure: {json.dumps(data, ensure_ascii=False)[:300]}"
            )
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
