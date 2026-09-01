import pytest

from framework_reader.llm.client import Message
from framework_reader.llm.guard import GuardedClient, OutboundTextError, PayloadGuard
from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
from framework_reader.llm.retry import RetryingClient

MSG = [Message(role="user", content="hi")]


class _Flaky:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.attempts = 0

    def complete(self, system, messages, *, model, max_tokens=4096,
                 response_format=None):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("厂商 503")
        return "终于成功"


def test_transient_failure_is_retried():
    inner = _Flaky(fail_times=2)
    client = RetryingClient(inner, attempts=3, sleep=lambda _: None)
    assert client.complete("s", MSG, model="m") == "终于成功"
    assert inner.attempts == 3


def test_gives_up_after_the_configured_attempts():
    inner = _Flaky(fail_times=99)
    client = RetryingClient(inner, attempts=3, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="503"):
        client.complete("s", MSG, model="m")
    assert inner.attempts == 3


def test_backoff_grows():
    slept: list[float] = []
    client = RetryingClient(_Flaky(99), attempts=4, base_delay=1.0, sleep=slept.append)
    with pytest.raises(RuntimeError):
        client.complete("s", MSG, model="m")
    assert slept == [1.0, 2.0, 4.0]


def test_red_line_violation_is_never_retried():
    """出口红线抛异常后不重试、不降级——W2 spec §3.4③、§6"""
    class _Guarded:
        def __init__(self) -> None:
            self.attempts = 0

        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            self.attempts += 1
            raise OutboundTextError("受版权原文即将出圈")

    inner = _Guarded()
    with pytest.raises(OutboundTextError):
        RetryingClient(inner, attempts=3, sleep=lambda _: None).complete(
            "s", MSG, model="m"
        )
    assert inner.attempts == 1


def test_registry_puts_retry_inside_the_guard():
    """红线断言只跑一次，且在任何请求发出之前。"""
    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    client = registry.build(
        "drafter", guard=PayloadGuard([]),
        key_lookup=lambda name: "sk-test",
    )
    assert isinstance(client, GuardedClient)
    assert isinstance(client._inner, RetryingClient)


def test_response_format_is_passed_through_to_the_inner_client():
    """Registry 组装的是 ``GuardedClient(RetryingClient(inner), guard)``，
    ``response_format`` 必须一路传到 inner——少一层整条链路就 TypeError。
    上一轮 outline 重导连续报
    ``RetryingClient.complete() got an unexpected keyword argument 'response_format'``
    就是这一层没接住。"""
    seen: list[dict | None] = []

    class _Recorder:
        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            seen.append(response_format)
            return "ok"

    RetryingClient(_Recorder(), sleep=lambda _: None).complete(
        "s", MSG, model="m",
        response_format={"type": "json_object"})
    assert seen == [{"type": "json_object"}]


def test_response_format_default_is_none_and_also_passed_through():
    """调用方不传时也要传 None 给 inner——inner 自己决定要不要用。"""
    seen: list[dict | None] = []

    class _Recorder:
        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            seen.append(response_format)
            return "ok"

    RetryingClient(_Recorder(), sleep=lambda _: None).complete("s", MSG, model="m")
    assert seen == [None]
