import pytest

from framework_reader.llm.client import FakeClient, Message


def test_fake_client_returns_queued_responses_in_order():
    client = FakeClient(["第一条", "第二条"])
    assert client.complete("sys", [Message(role="user", content="a")], model="m") == "第一条"
    assert client.complete("sys", [Message(role="user", content="b")], model="m") == "第二条"


def test_fake_client_records_calls_for_assertions():
    client = FakeClient(["x"])
    client.complete("你是助手", [Message(role="user", content="问题")], model="m", max_tokens=99)
    assert client.calls == [{
        "system": "你是助手",
        "messages": [{"role": "user", "content": "问题"}],
        "model": "m",
        "max_tokens": 99,
        "response_format": None,
    }]


def test_fake_client_runs_out_loudly():
    """测试夹具耗尽必须炸，不能悄悄返回空串让断言假通过。"""
    client = FakeClient(["only"])
    client.complete("s", [], model="m")
    with pytest.raises(AssertionError):
        client.complete("s", [], model="m")
