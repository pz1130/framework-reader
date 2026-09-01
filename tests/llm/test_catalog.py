"""厂商模型目录。见 docs/superpowers/specs/2026-08-24-model-catalog-design.md

这是第二类出网：不携带任何内容（没有控制条款、没有制度正文），只问
「你这儿有哪些模型」。真实请求收在 `_default_get` 一个函数里，测试一律注入假的。
"""
import pytest

from framework_reader.llm.catalog import CatalogError, fetch_models
from framework_reader.llm.registry import ProviderPreset

DEEPSEEK = ProviderPreset(
    id="deepseek", kind="openai_compat", base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY", default_model="deepseek-chat")
CLAUDE = ProviderPreset(
    id="anthropic", kind="anthropic", base_url="",
    api_key_env="ANTHROPIC_API_KEY", default_model="claude-opus-5")


def _fake(payload, *, seen=None):
    def get(url, headers):
        if seen is not None:
            seen.append((url, headers))
        return payload
    return get


def test_openai_format_is_parsed():
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}))
    assert got == ["deepseek-chat", "deepseek-reasoner"]


def test_it_asks_the_right_url_with_a_bearer_token():
    seen = []
    fetch_models(DEEPSEEK, "sk-x", http_get=_fake({"data": []}, seen=seen))
    url, headers = seen[0]
    assert url == "https://api.deepseek.com/models"
    assert headers["Authorization"] == "Bearer sk-x"


def test_anthropic_uses_its_own_url_and_headers():
    """anthropic 的 base_url 是空的，且它要 x-api-key 与 anthropic-version。"""
    seen = []
    fetch_models(CLAUDE, "sk-ant-x", http_get=_fake({"data": []}, seen=seen))
    url, headers = seen[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "sk-ant-x"
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


def test_results_are_sorted_and_deduped():
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}))
    assert got == ["a", "b"]


def test_entries_without_an_id_are_skipped_not_fatal():
    """厂商多返回一个字段是常事，少一个 id 不该让整次拉取失败。"""
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "a"}, {"object": "model"}, {"id": ""}]}))
    assert got == ["a"]


def test_an_empty_catalog_is_not_an_error():
    assert fetch_models(DEEPSEEK, "sk-x", http_get=_fake({"data": []})) == []


@pytest.mark.parametrize("payload", [{}, {"data": "不是列表"}, {"models": []}])
def test_a_shape_we_do_not_understand_counts_as_unsupported(payload):
    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=_fake(payload))
    assert exc.value.kind == "unsupported"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"),
                                         (404, "unsupported"), (500, "unreachable")])
def test_http_errors_map_to_three_kinds(status, kind):
    def boom(url, headers):
        raise _HttpStatus(status)

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=boom)
    assert exc.value.kind == kind


def test_any_other_exception_is_unreachable():
    def boom(url, headers):
        raise TimeoutError("超时")

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=boom)
    assert exc.value.kind == "unreachable"


def test_the_key_never_appears_in_the_error_message():
    """异常消息会被原样渲到页面上。"""
    def boom(url, headers):
        raise _HttpStatus(401)

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-live-0123456789abcdef", http_get=boom)
    assert "sk-live-0123456789abcdef" not in str(exc.value)
    assert "0123456789abcdef" not in str(exc.value)


# ---------- 过滤 ----------

@pytest.mark.parametrize("model_id", [
    "text-embedding-3-large", "BAAI/bge-reranker-v2-m3", "tts-1",
    "whisper-large-v3", "omni-moderation-latest", "black-forest-labs/FLUX.1",
    "stable-diffusion-3", "gpt-image-1",
])
def test_non_chat_models_are_filtered_out(model_id):
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": model_id}, {"id": "deepseek-chat"}]}))
    assert got == ["deepseek-chat"]


@pytest.mark.parametrize("model_id", [
    "deepseek-chat", "deepseek-reasoner", "qwen-max", "glm-4-plus",
    "kimi-latest", "claude-opus-5", "gpt-4o", "llama-3.3-70b-versatile",
    "deepseek-ai/DeepSeek-V3", "mistral-large-latest", "step-2-16k",
])
def test_real_chat_models_survive_the_filter(model_id):
    """过滤清单会误伤——用真实模型名钉住它至少不误伤这些。"""
    assert fetch_models(DEEPSEEK, "sk-x",
                        http_get=_fake({"data": [{"id": model_id}]})) == [model_id]


class _HttpStatus(Exception):
    """假的 HTTP 状态异常，形状与 httpx.HTTPStatusError 对齐（有 .response.status_code）。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()
