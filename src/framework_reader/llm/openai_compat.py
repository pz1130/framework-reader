"""OpenAI-compatible adapter. W2 spec §3.1

One adapter covers deepseek / qwen / glm / kimi / doubao / hunyuan / minimax /
baichuan / siliconflow / openai — they all offer /chat/completions.
"""
import json
from collections.abc import Callable

from framework_reader.llm.client import Message

HttpPost = Callable[[str, dict, dict], dict]


def _is_minimax_m3(model: str) -> bool:
    return "minimax-m3" in (model or "").lower()


def build_payload(
    system: str, messages: list[Message], model: str, max_tokens: int,
    response_format: dict | None = None,
) -> dict:
    body: list[dict] = []
    if system.strip():
        body.append({"role": "system", "content": system})
    body.extend(m.model_dump() for m in messages)
    payload = {"model": model, "messages": body, "max_tokens": max_tokens}
    # response_format is an optional field widely supported by OpenAI-compatible
    # vendors (minimax, deepseek, qwen, glm, kimi… nearly all followed along). With
    # ``json_object`` on, the model is forced to reply with JSON only; a drafting call
    # like "write a passage of prose" should not pass it — the caller decides whether
    # to pass it, and this function only forwards it.
    if response_format is not None:
        payload["response_format"] = response_format
    # MiniMax-M3 turns thinking on by default, and the thinking lands in the <think>
    # section of content. During NIST.AI.100-1 import, all 8192 tokens / 120 s were
    # burned on thinking, no JSON came out, and the third chunk died with ReadTimeout.
    # M3 can turn it off; M2.x cannot, and other vendors 400 on this field — so it is
    # pinned to M3 only.
    if _is_minimax_m3(model):
        payload["thinking"] = {"type": "disabled"}
    return payload


# Drafting one interpretation asks the model to write several hundred words; two minutes
# is not long. The probe makes its own trade-off — see probe.py.
CHAT_TIMEOUT_SECONDS = 120.0


def _default_post(
    url: str, headers: dict, payload: dict, timeout: float = CHAT_TIMEOUT_SECONDS
) -> dict:
    import httpx

    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        http_post: HttpPost | None = None,
        timeout: float = CHAT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        # An injected fake post takes only three arguments (tests should not care about
        # timeouts), so the timeout is bound on here.
        self._post = http_post or (
            lambda url, headers, body: _default_post(url, headers, body, timeout))

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        payload = build_payload(system, messages, model, max_tokens, response_format)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = self._post(f"{self._base_url}/chat/completions", headers, payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"provider returned an unexpected structure: {json.dumps(data, ensure_ascii=False)[:300]}"
            ) from exc
        # With reasoning_split on, MiniMax may return content as null with the JSON in
        # reasoning_content. content wins — that is the "said out loud" part.
        # An empty string is a legitimate reply (the probe run where thinking burned all
        # the tokens and the body came back empty).
        if not isinstance(message, dict):
            raise RuntimeError(
                f"provider returned an unexpected structure: {json.dumps(data, ensure_ascii=False)[:300]}"
            )
        content = message.get("content")
        if content:
            return content
        reasoning = message.get("reasoning_content")
        if reasoning:
            return reasoning
        return content or ""
