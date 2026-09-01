from framework_reader.blindtest.variants import leak_hits, render_bare
from framework_reader.llm.client import FakeClient


def _call(response: str = "这是模型的回答"):
    client = FakeClient([response])
    text = render_bare(
        client,
        control_id="NIST-CSF-2.0:DE.CM-01",
        outcome="Networks and network services are monitored",
        model="deepseek-chat",
    )
    return client, text


def test_returns_the_model_answer_verbatim():
    _, text = _call("监控要覆盖全部网段并有人处置告警")
    assert text == "监控要覆盖全部网段并有人处置告警"


def test_the_question_looks_like_a_peer_asking_a_chatbot():
    """spec §1：朴素提示词。给对照组精心调过的提示词是造一个不存在的对手。"""
    client, _ = _call()
    asked = client.calls[0]["messages"][0]["content"]
    assert "DE.CM-01" in asked
    assert "Networks and network services are monitored" in asked
    assert "audit" in asked


def test_bare_prompt_gives_no_product_structure():
    """不得把七字段结构送给对照组——那是产品的一部分。"""
    client, _ = _call()
    whole = client.calls[0]["system"] + client.calls[0]["messages"][0]["content"]
    for field in ("common_myth", "auditor_asks", "regional_note", "plain_zh"):
        assert field not in whole


def test_bare_output_is_checked_for_leaks():
    _, text = _call("回答里不该有 provenance 这种词")
    assert leak_hits(text) == ["provenance"], "泄露检测要能作用在模型输出上"


def test_prompt_version_is_pinned():
    from framework_reader.prompts import PROMPT_VERSIONS

    assert PROMPT_VERSIONS["bare_llm"] == "2026.08-b1"
