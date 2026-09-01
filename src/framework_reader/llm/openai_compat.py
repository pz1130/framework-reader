"""OpenAI 兼容适配器。W2 spec §3.1

一个适配器覆盖 deepseek / qwen / glm / kimi / doubao / hunyuan / minimax /
baichuan / siliconflow / openai —— 它们都提供 /chat/completions。
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
    # response_format 是 OpenAI 兼容厂商广泛支持的可选字段（minimax、
    # deepseek、qwen、glm、kimi…几乎都跟了）。开了 ``json_object`` 模型
    # 强制只回 JSON，drafting 那种「写一段散文」的调用不应该传——在
    # 调用方决定要不要传，这里只负责透传。
    if response_format is not None:
        payload["response_format"] = response_format
    # MiniMax-M3 默认开 thinking，思考写进 content 的 <think> 里。
    # NIST.AI.100-1 导入时 8192 token / 120s 全烧在思考上，JSON 出不来，
    # 第三块直接 ReadTimeout。M3 可以关；M2.x 关不掉，别的厂商带这个
    # 字段会 400——所以只钉 M3。
    if _is_minimax_m3(model):
        payload["thinking"] = {"type": "disabled"}
    return payload


# 起草一条解读要模型写好几百字，两分钟不算久。探针另有取舍，见 probe.py。
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
        # 注入的假 post 只有三个参数（测试不该关心超时），所以超时在这里绑上去。
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
        # MiniMax 开 reasoning_split 时 content 可能是 null，JSON 在
        # reasoning_content 里。content 优先——那才是「说出口的」。
        # 空字符串是合法回复（探针那次「思考把 token 用光、正文为空」）。
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
