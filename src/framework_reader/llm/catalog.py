"""厂商模型目录：问「你这儿有哪些模型」。见 2026-08-24 模型目录设计

**这是第二类出网。** 携带内容（控制条款、解读、配套文档节选）的出网只有
`llm/registry.py` 组装、被 `GuardedClient` 包住那一条；这里一个字的内容都不带，
只发一个 GET 和一把 key。同类的还有 `identity/entra.py` 的 OIDC。
规矩是一样的：真实请求收在 `_default_get` 一个函数里，可注入替换，
且有测试断言没有任何测试碰它。

不碰数据库。缓存是 `llm/config.py` 的事——这里只负责「问到了什么」。
"""
from collections.abc import Callable

from framework_reader.llm.registry import ProviderPreset

HttpGet = Callable[[str, dict], dict]

# 目录查询等 15 秒还不回，就当它不支持。chat 那边是 120 秒，
# 那是给模型生成留的时间，跟列目录不是一回事。
TIMEOUT_SECONDS = 15.0

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# 按 id 子串剔除。siliconflow 与 openrouter 的目录里有一两百条这类模型，
# 它们永远不会被用作 drafter，留在下拉里只会让人翻不到想要的那条。
#
# **这份清单会误伤**：某天某家把对话模型起名带 `vision-ocr`，它就被吃掉了。
# 代价可接受，因为手填框永远保留——误伤的后果是「下拉里没有，手填一下」，
# 不是「用不了」。
NON_CHAT_MARKERS = (
    "embed", "rerank", "tts", "whisper", "audio", "moderation",
    "image", "vision-ocr", "stable-diffusion", "flux",
)


class CatalogError(Exception):
    """拉目录失败。`kind` 决定页面上说什么话。

    - `auth`        —— 这把 key 被拒了（401/403）
    - `unsupported` —— 这家不提供目录，或返回的形状我们不认识（404/解析不出）
    - `unreachable` —— 超时、连不上、5xx
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _default_get(url: str, headers: dict) -> dict:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _request(preset: ProviderPreset, api_key: str) -> tuple[str, dict]:
    if preset.kind == "anthropic":
        return ANTHROPIC_MODELS_URL, {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return f"{preset.base_url.rstrip('/')}/models", {
        "Authorization": f"Bearer {api_key}",
    }


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in NON_CHAT_MARKERS)


def fetch_models(
    preset: ProviderPreset, api_key: str, *, http_get: HttpGet | None = None
) -> list[str]:
    """问一次目录，回一份排好序、去过重、滤掉非对话模型的 id 列表。

    **异常消息里绝不出现 key。** 这个消息会被原样渲到页面上。
    """
    get = http_get or _default_get
    url, headers = _request(preset, api_key)
    try:
        payload = get(url, headers)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要翻译成三种之一
        status = _status_of(exc)
        if status in (401, 403):
            raise CatalogError(
                "auth",
                f"{preset.id} rejected this key. If you are sure it is correct, "
                "the provider's catalog API may need extra permissions - the key is saved, and drafting still works.",
            ) from None
        if status == 404:
            raise CatalogError(
                "unsupported", f"{preset.id} provides no model catalog; enter the model name manually.") from None
        raise CatalogError(
            "unreachable", f"Could not reach {preset.id}; you can click Refresh later.") from None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise CatalogError(
            "unsupported",
            f"{preset.id} returned an unrecognized catalog format; enter the model name manually.")

    ids = {
        str(row["id"]).strip()
        for row in data
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    return sorted(m for m in ids if is_chat_model(m))
