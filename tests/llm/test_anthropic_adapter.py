import pytest

from framework_reader.llm.anthropic_adapter import AnthropicClient, build_payload
from framework_reader.llm.client import Message


def test_system_is_a_top_level_block_not_a_message():
    payload = build_payload("你是助手", [Message(role="user", content="问题")], "claude-opus-5", 512, True)
    assert payload["messages"] == [{"role": "user", "content": "问题"}]
    assert payload["system"][0]["text"] == "你是助手"


def test_system_block_carries_cache_control_when_enabled():
    """固定前缀（system + 黄金样例）缓存后成本与延迟都能砍掉大半。W2 spec §3.4①"""
    payload = build_payload("长前缀", [], "claude-opus-5", 10, True)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_control_can_be_turned_off():
    payload = build_payload("长前缀", [], "claude-opus-5", 10, False)
    assert "cache_control" not in payload["system"][0]


def test_empty_system_omits_the_block_entirely():
    payload = build_payload("", [Message(role="user", content="x")], "m", 10, True)
    assert "system" not in payload


def test_complete_returns_concatenated_text_blocks():
    def send(payload: dict) -> dict:
        return {"content": [{"type": "text", "text": "前半"}, {"type": "text", "text": "后半"}]}

    client = AnthropicClient("sk-ant-test", send=send)
    assert client.complete("s", [Message(role="user", content="x")], model="m") == "前半后半"


def test_unexpected_response_shape_raises():
    client = AnthropicClient("k", send=lambda payload: {"error": {"message": "overloaded"}})
    with pytest.raises(RuntimeError, match="overloaded"):
        client.complete("s", [Message(role="user", content="x")], model="m")
