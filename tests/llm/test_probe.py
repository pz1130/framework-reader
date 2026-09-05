"""「测一下」：拿此刻选的厂商+模型真发一次最小请求，回答「它到底能不能用」。

这是**第一类出网**（走 chat 端点），但 payload 是一句固定的问候，
一个字的框架内容都不带——所以它照样被 GuardedClient 包着，
守卫的红线断言必须在请求发出之前跑过。

探针**不重试**。重试只会让「不通」慢三倍，而按下按钮的人要的是此刻的答案。
"""
import pytest

from framework_reader.llm.probe import PROBE_PROMPT, probe_model
from framework_reader.llm.registry import ProviderPreset

DEEPSEEK = ProviderPreset(
    id="deepseek", kind="openai_compat", base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY", default_model="deepseek-chat")
CLAUDE = ProviderPreset(
    id="anthropic", kind="anthropic", base_url="",
    api_key_env="ANTHROPIC_API_KEY", default_model="claude-opus-5")

KEY = "sk-live-0123456789abcdef"


def _replies(text, *, seen=None):
    def post(url, headers, payload):
        if seen is not None:
            seen.append((url, headers, payload))
        return {"choices": [{"message": {"content": text}}]}
    return post


# ---------- 通了 ----------

def test_a_reply_means_it_works():
    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=_replies("好"))
    assert got.ok
    assert got.kind == "ok"
    assert got.reply == "好"


def test_it_reports_how_long_the_round_trip_took():
    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=_replies("好"))
    assert got.elapsed_ms >= 0


def test_an_empty_reply_still_counts_as_working():
    """推理模型可能把这几个 token 全花在思考上，正文是空的。

    端点通了、key 认了、模型名认了——起草时给 4096 token 它就写得出来。
    拿「这次没回字」去判它不能用，会把一整类模型误杀。
    """
    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=_replies(""))
    assert got.ok
    assert got.reply == ""


def test_the_thinking_out_loud_is_not_what_it_said():
    """推理模型会把思维链塞进正文。实测 MiniMax-M2.7 用 16 个 token 想了一半，
    回显就变成了「它回了：<think>The user is speaking Chinese...」——
    那不是它回的话，是它的草稿纸。"""
    got = probe_model(DEEPSEEK, "deepseek-reasoner", KEY,
                      http_post=_replies("<think>这人要我回一个字</think>好"))
    assert got.reply == "好"


def test_an_unfinished_thought_leaves_nothing_to_show():
    """token 不够，思维链没写完就被截断——后面根本没有正文。

    这时候要说「它没回字」，不能把半句草稿当成答案端出去。
    """
    got = probe_model(DEEPSEEK, "deepseek-reasoner", KEY,
                      http_post=_replies("<think>这人要我回一"))
    assert got.reply == ""
    assert got.ok


def test_a_long_reply_is_truncated_for_the_page():
    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=_replies("好" * 500))
    assert len(got.reply) <= 120


# ---------- 发出去的是什么 ----------

def test_the_probe_carries_no_framework_content():
    """出网红线：这次请求里不许有控制条款、解读、制度正文。

    断言的是「发出去的就是那句固定问候」——多一个字都要在这里显形。
    """
    seen = []
    probe_model(DEEPSEEK, "deepseek-chat", KEY,
                http_post=_replies("好", seen=seen))
    _, _, payload = seen[0]
    assert payload["messages"] == [{"role": "user", "content": PROBE_PROMPT}]
    assert payload["model"] == "deepseek-chat"
    assert payload["max_tokens"] <= 32


def test_it_probes_the_model_you_gave_it_not_the_preset_default():
    """页面上填的是 MiniMax-M2，探针却去测 default_model，等于什么都没验。"""
    seen = []
    probe_model(DEEPSEEK, "deepseek-reasoner", KEY,
                http_post=_replies("好", seen=seen))
    assert seen[0][2]["model"] == "deepseek-reasoner"


def test_anthropic_goes_through_its_own_adapter():
    seen = []

    def send(payload):
        seen.append(payload)
        return {"content": [{"type": "text", "text": "好"}]}

    got = probe_model(CLAUDE, "claude-opus-5", KEY, send=send)
    assert got.ok and got.reply == "好"
    assert seen[0]["model"] == "claude-opus-5"


# ---------- 不通 ----------

@pytest.mark.parametrize("status,kind", [
    (401, "auth"), (403, "auth"),
    (400, "unsupported"), (404, "unsupported"),
    (429, "unreachable"), (500, "unreachable"), (503, "unreachable"),
])
def test_http_errors_map_to_three_kinds(status, kind):
    def boom(url, headers, payload):
        raise _HttpStatus(status)

    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=boom)
    assert not got.ok
    assert got.kind == kind


def test_a_timeout_is_unreachable():
    def boom(url, headers, payload):
        raise TimeoutError("超时")

    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=boom)
    assert got.kind == "unreachable"


def test_a_shape_we_do_not_understand_is_not_a_pass():
    """端点回了 200，但结构不认识。这不叫「能用」。"""
    def weird(url, headers, payload):
        return {"输出": "好"}

    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=weird)
    assert not got.ok
    assert got.kind == "unsupported"


def test_a_bad_model_name_says_which_model_it_was():
    """「不认这个模型名」和「key 不对」要能一眼分开，否则人只会瞎换 key。"""
    def boom(url, headers, payload):
        raise _HttpStatus(400)

    got = probe_model(DEEPSEEK, "no-such-model", KEY, http_post=boom)
    assert "no-such-model" in got.message


# ---------- 不重试 ----------

def test_the_probe_does_not_retry():
    """RetryingClient 会试三次、每次之间还退避睡一觉。

    按下按钮的人在等，而三次 500 和一次 500 说明的是同一件事。
    """
    calls = []

    def boom(url, headers, payload):
        calls.append(1)
        raise _HttpStatus(500)

    probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=boom)
    assert len(calls) == 1


# ---------- key 不外泄 ----------

@pytest.mark.parametrize("status", [401, 400, 500])
def test_the_key_never_appears_in_what_the_page_will_show(status):
    """message 和 reply 都会被原样渲到页面上。"""
    def boom(url, headers, payload):
        raise _HttpStatus(status)

    got = probe_model(DEEPSEEK, "deepseek-chat", KEY, http_post=boom)
    assert KEY not in got.message
    assert "0123456789abcdef" not in got.message


class _HttpStatus(Exception):
    """假的 HTTP 状态异常，形状与 httpx.HTTPStatusError 对齐（有 .response.status_code）。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()
