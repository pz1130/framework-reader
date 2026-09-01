import pytest

from framework_reader.llm.client import Message
from framework_reader.llm.openai_compat import OpenAICompatClient, build_payload


def test_system_prompt_becomes_the_first_message():
    payload = build_payload("你是助手", [Message(role="user", content="问题")], "deepseek-chat", 512)
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1] == {"role": "user", "content": "问题"}
    assert payload["model"] == "deepseek-chat"
    assert payload["max_tokens"] == 512


def test_empty_system_prompt_is_omitted():
    payload = build_payload("", [Message(role="user", content="问题")], "m", 10)
    assert payload["messages"] == [{"role": "user", "content": "问题"}]


def _recorder(captured: list):
    def post(url: str, headers: dict, payload: dict) -> dict:
        captured.append({"url": url, "headers": headers, "payload": payload})
        return {"choices": [{"message": {"content": "模型回答"}}]}
    return post


def test_request_goes_to_chat_completions_with_bearer_key():
    captured: list = []
    client = OpenAICompatClient(
        "https://api.deepseek.com", "sk-test", http_post=_recorder(captured)
    )
    out = client.complete("sys", [Message(role="user", content="hi")], model="deepseek-chat")
    assert out == "模型回答"
    assert captured[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_trailing_slash_in_base_url_does_not_double_up():
    captured: list = []
    client = OpenAICompatClient(
        "https://api.moonshot.cn/v1/", "k", http_post=_recorder(captured)
    )
    client.complete("s", [Message(role="user", content="x")], model="kimi-latest")
    assert captured[0]["url"] == "https://api.moonshot.cn/v1/chat/completions"


def test_unexpected_response_shape_raises_instead_of_returning_empty():
    def bad_post(url, headers, payload):
        return {"error": {"message": "quota exceeded"}}

    client = OpenAICompatClient("https://x", "k", http_post=bad_post)
    with pytest.raises(RuntimeError, match="quota exceeded"):
        client.complete("s", [Message(role="user", content="x")], model="m")


def test_response_format_when_set_is_included_in_payload():
    payload = build_payload(
        "你是助手", [Message(role="user", content="q")], "m", 512,
        response_format={"type": "json_object"})
    assert payload["response_format"] == {"type": "json_object"}


def test_response_format_when_omitted_is_not_in_payload():
    payload = build_payload("你是助手", [Message(role="user", content="q")], "m", 512)
    assert "response_format" not in payload


def test_minimax_m3_disables_thinking_so_outline_gets_json_not_a_scratchpad():
    """MiniMax-M3 默认开 thinking，思考写进 content 的 <think> 里。
    NIST.AI.100-1 三次切分把 8192 token / 120s 全烧在思考上，JSON
    出不来。M3 可以关；别的模型不要带这个字段——有的厂商会 400。"""
    payload = build_payload("sys", [Message(role="user", content="q")],
                            "MiniMax-M3", 512)
    assert payload["thinking"] == {"type": "disabled"}


def test_other_models_do_not_get_a_thinking_field():
    for model in ("deepseek-chat", "MiniMax-M2.7", "abab6.5s-chat", "qwen-max"):
        payload = build_payload("sys", [Message(role="user", content="q")],
                                model, 512)
        assert "thinking" not in payload, model


def test_null_content_falls_back_to_reasoning_content():
    """reasoning_split 打开时 content 可能是 null，JSON 在
    reasoning_content 里。取 content 直接 KeyError/TypeError 不是这回事。"""
    def post(url, headers, payload):
        return {"choices": [{"message": {
            "content": None,
            "reasoning_content": '[{"ref":"1"}]',
        }}]}

    client = OpenAICompatClient("https://x", "k", http_post=post)
    assert client.complete("s", [Message(role="user", content="x")],
                           model="MiniMax-M3") == '[{"ref":"1"}]'
