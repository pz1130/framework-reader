"""「测一下」：拿一组厂商+模型真发一次最小请求，回答「它到底能不能用」。

目录（`catalog.py`）回答的是「这家有哪些模型」，那是**第二类出网**，不带内容。
探针走的是 chat 端点，和起草同一条路——所以它是**第一类出网**，照样被
`GuardedClient` 包着。区别只在 payload：一句固定的问候，一个字的框架内容都没有。
`tests/llm/test_probe.py` 逐字钉住了发出去的是什么。

这个文件自己不发请求：真实请求仍然收在两个适配器各自的 `_default_*` 里，
探针只是组装 client。所以出网点清单（`tests/test_no_network_in_tests.py`
里那份白名单）**一个字都不用改**——要是它红了，说明这个设计走错了。

**不重试。** `registry.build()` 会包一层 `RetryingClient`——那是给批量起草用的，
三次退避加起来能拖上几分钟。按下「测一下」的人在盯着屏幕等，而三次 500
和一次 500 说明的是同一件事。所以这里自己组装 client，不走 `build()`。
"""
import time
from dataclasses import dataclass

from framework_reader.llm.client import Message
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.registry import ProviderPreset

# 探针问的这一句。**它必须与任何框架内容无关**，且短到一眼能看出无关。
#
# 用中文而不是 `ping`，因为这个产品的正文全是中文：一个连中文都回不利索的
# 模型，「端点通了」并不说明它能用来起草。让人**看见它回的字**，比看见一个
# 绿勾多说明一件事。
PROBE_PROMPT = "Reply with one word: ok"

# 回显截断。探针只要证明「它说话了」，不是一个聊天窗。
REPLY_LIMIT = 120

# 目录查询 15 秒，起草 120 秒。探针介于两者之间：它要发一次真实推理，
# 但按钮不能挂着不动。见 `catalog.TIMEOUT_SECONDS` 同一套取舍。
TIMEOUT_SECONDS = 20.0

MAX_TOKENS = 16


@dataclass(frozen=True)
class ProbeResult:
    """`message` 与 `reply` 都会被原样渲到页面上。**两者都不许出现 key。**"""

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
    # 空守卫：payload 是上面那个常量，不含任何原文。包着它是为了让
    # 「所有 chat 出网都过 GuardedClient」这句话没有例外——有例外的规矩记不住。
    return GuardedClient(inner, PayloadGuard([]))


def probe_model(
    preset: ProviderPreset,
    model: str,
    api_key: str,
    *,
    http_post=None,
    send=None,
) -> ProbeResult:
    """发一次最小请求。**从不抛异常**——调用方是一个按钮，它要的是一句话。

    `http_post` / `send` 只为测试注入，与 `catalog.fetch_models` 同一个模式。
    """
    client = _build(preset, api_key, http_post, send)
    started = time.monotonic()
    try:
        reply = client.complete(
            "", [Message(role="user", content=PROBE_PROMPT)],
            model=model, max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要翻译成三种之一
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
    """把思维链剥掉，只留它真正说出口的那部分。

    推理模型（deepseek-reasoner、MiniMax-M2.7 等）把草稿纸塞在正文里。
    实测 MiniMax-M2.7 用这十几个 token 想了一半就被截断，回显于是变成
    「它回了：`<think>The user is speaking Chinese…`」——那不是它的答话。

    没闭合的 `<think>` 说明后面根本没有正文，整段都是草稿：剥成空的。
    页面对空回复有话说（「它没回字」），那句话是诚实的；
    把半句草稿端出去不是。
    """
    import re

    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    return re.sub(r"<think>.*$", "", text, flags=re.S).strip()


def _failure(
    preset: ProviderPreset, model: str, exc: Exception, elapsed: int
) -> ProbeResult:
    """**异常原文一律不进 message。** 它可能带上请求细节，而人只需要知道哪一环坏了。"""
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
        # 适配器解不出结构时抛的就是 RuntimeError。端点回了 200，但不是我们认识的形状。
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
