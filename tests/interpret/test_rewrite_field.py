"""按用户的一句要求重写一个字段。2026-08-23

用户看得出哪儿不对，但未必想自己动笔。他给方向，模型执行。
产出仍是 AI 写的——**要求是他提的，字是模型写的**。
"""
import json

import pytest

from framework_reader.interpret.drafter import DrafterOutputError, rewrite_field
from framework_reader.llm.client import FakeClient


def _call(response: str, *, field="intent", current="旧的", instruction="再具体点"):
    client = FakeClient([response])
    value = rewrite_field(
        client, control_id="ACME-1:4.1", field=field, label="这条在防什么",
        current=current, instruction=instruction, model="m",
        outcome="日志集中采集，留存六个月。",
    )
    return value, client


def test_a_rewritten_string_comes_back():
    value, _ = _call(json.dumps({"value": "新的、更具体的说法"}, ensure_ascii=False))
    assert value == "新的、更具体的说法"


def test_your_instruction_reaches_the_model():
    """要求没进 payload，这个功能就是个装饰。"""
    _, client = _call(
        json.dumps({"value": "x"}), instruction="带上系统名，别写「相关系统」"
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "带上系统名" in sent


def test_the_clause_body_is_the_grounding():
    _, client = _call(json.dumps({"value": "x"}))
    assert "留存六个月" in client.calls[0]["messages"][0]["content"]


def test_the_current_text_is_shown_so_it_rewrites_not_reinvents():
    _, client = _call(json.dumps({"value": "x"}), current="当前这版内容")
    assert "当前这版内容" in client.calls[0]["messages"][0]["content"]


def test_three_rungs_stay_three():
    value, _ = _call(
        json.dumps({"value": {"1": "一", "2": "二", "3": "三"}}, ensure_ascii=False),
        field="practice", current={"1": "a", "2": "b", "3": "c"},
    )
    assert value == {"1": "一", "2": "二", "3": "三"}


def test_a_collapsed_practice_is_refused():
    """形状塌了就退回。三档变成一句话，差距报告的「下一步」当场失效。"""
    with pytest.raises(DrafterOutputError, match="three-level dict"):
        _call(json.dumps({"value": "塌成一句话了"}, ensure_ascii=False),
              field="practice", current={"1": "a", "2": "b", "3": "c"})


def test_a_list_field_must_stay_a_list():
    with pytest.raises(DrafterOutputError, match="auditor_asks"):
        _call(json.dumps({"value": "不是数组"}, ensure_ascii=False),
              field="auditor_asks", current=["问题一"])


def test_a_response_without_value_is_refused():
    with pytest.raises(DrafterOutputError, match="value"):
        _call(json.dumps({"intent": "写错键了"}, ensure_ascii=False))


def test_an_empty_instruction_never_reaches_the_model():
    """没有要求就不必重写，更不必为它付一次调用。"""
    client = FakeClient([json.dumps({"value": "x"})])
    with pytest.raises(DrafterOutputError, match="instruction must not be empty"):
        rewrite_field(client, control_id="ACME-1:4.1", field="intent",
                      label="这条在防什么", current="旧的", instruction="   ",
                      model="m")
    assert client.calls == []
