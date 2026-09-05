import json

import pytest

from framework_reader.interpret.drafter import DrafterOutputError, draft_fields
from framework_reader.interpret.model import Basis, DIFFERENTIATING_FIELDS, DRAFTED_FIELDS
from framework_reader.llm.client import FakeClient

GOOD = json.dumps({
    "intent": "防的是供方在关系存续期变质",
    "plain_zh": "签完合同还得持续盯着供方",
    "practice": {"1": "有台账", "2": "定期复核", "3": "指标化并联动合同"},
    "evidence": "供方复核记录与签字",
}, ensure_ascii=False)


def _draft(response: str):
    return draft_fields(
        FakeClient([response]),
        control_id="NIST-CSF-2.0:GV.SC-07",
        outcome="The risks posed by a supplier ... are monitored",
        neighbors=["NIST-800-53-R5:SR-6"],
        model="claude-opus-5",
    )


def test_produces_exactly_the_four_drafted_fields():
    fields = _draft(GOOD)
    assert set(fields) == set(DRAFTED_FIELDS)


def test_drafted_fields_are_marked_inferred():
    for field in _draft(GOOD).values():
        assert field.basis is Basis.INFERRED


def test_practice_keeps_three_maturity_levels():
    assert set(_draft(GOOD)["practice"].value) == {"1", "2", "3"}


def test_differentiating_fields_are_discarded_even_if_the_model_volunteers_them():
    """D1 的第一道闸：起草器不得为这三个字段供稿。W2 spec §1 D1"""
    payload = json.loads(GOOD)
    payload["common_myth"] = "模型自作主张写的误解"
    payload["auditor_asks"] = ["模型编的追问"]
    fields = _draft(json.dumps(payload, ensure_ascii=False))
    assert set(fields).isdisjoint(DIFFERENTIATING_FIELDS)


def test_control_id_and_outcome_reach_the_prompt():
    client = FakeClient([GOOD])
    draft_fields(
        client,
        control_id="NIST-CSF-2.0:GV.SC-07",
        outcome="供方风险在关系存续期被监控",
        neighbors=["NIST-800-53-R5:SR-6"],
        model="m",
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "GV.SC-07" in sent
    assert "供方风险在关系存续期被监控" in sent
    assert "NIST-800-53-R5:SR-6" in sent


def test_non_json_response_raises_instead_of_guessing():
    with pytest.raises(DrafterOutputError):
        _draft("模型没按格式回，写了一段散文。")


def test_missing_required_field_raises():
    payload = json.loads(GOOD)
    del payload["evidence"]
    with pytest.raises(DrafterOutputError, match="evidence"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_practice_without_three_levels_raises():
    payload = json.loads(GOOD)
    payload["practice"] = {"1": "只有一档"}
    with pytest.raises(DrafterOutputError, match="practice"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_fenced_json_is_tolerated():
    """模型爱套 ```json 围栏，这个不算格式错误。"""
    fields = _draft(f"```json\n{GOOD}\n```")
    assert set(fields) == set(DRAFTED_FIELDS)


def test_evidence_returned_as_a_dict_is_rejected():
    """实测 MiniMax 会把 evidence 也按三档返回。

    Field.value 的类型是 str | list | dict | None，Pydantic 收得下，所以
    schema 拦不住——必须在起草器这里逐字段验型，否则坏形状一路进库。
    """
    payload = json.loads(GOOD)
    payload["evidence"] = {"1": "日志", "2": "响应记录", "3": "IDS 报告"}
    with pytest.raises(DrafterOutputError, match="evidence"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_intent_returned_as_a_list_is_rejected():
    payload = json.loads(GOOD)
    payload["intent"] = ["防这个", "也防那个"]
    with pytest.raises(DrafterOutputError, match="intent"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_practice_level_values_must_be_strings():
    payload = json.loads(GOOD)
    payload["practice"] = {"1": ["一"], "2": "二", "3": "三"}
    with pytest.raises(DrafterOutputError, match="practice"):
        _draft(json.dumps(payload, ensure_ascii=False))


FULL = json.dumps({
    "intent": "防的是把工具当结果",
    "plain_zh": "上了工具还得有人看、有规则",
    "practice": {"1": "一", "2": "二", "3": "三"},
    "evidence": "监控覆盖清单与告警处置记录",
    "common_myth": "以为上了 IDS 就等于做到了监控",
    "auditor_asks": ["哪些网段没纳入", "告警之后谁看"],
    "regional_note": None,
}, ensure_ascii=False)


def _draft_full(response: str):
    from framework_reader.interpret.drafter import draft_full_fields

    return draft_full_fields(
        FakeClient([response]),
        control_id="NIST-CSF-2.0:DE.CM-01",
        outcome="Networks and network services are monitored",
        neighbors=["NIST-800-53-R5:SI-4"],
        model="m",
    )


def test_full_drafter_produces_all_seven_fields():
    from framework_reader.interpret.model import ALL_FIELDS

    assert set(_draft_full(FULL)) == set(ALL_FIELDS)


def test_full_drafter_marks_everything_inferred():
    """B 路线的诚信约束：AI 写的一律 inferred，不得标 practitioner。主 spec §5"""
    for field in _draft_full(FULL).values():
        assert field.basis is Basis.INFERRED


def test_full_drafter_allows_null_regional_note():
    """很多控制确实没有地域差异，编一个比留空更糟。"""
    assert _draft_full(FULL)["regional_note"].value is None


def test_full_drafter_requires_auditor_asks_to_be_a_list():
    payload = json.loads(FULL)
    payload["auditor_asks"] = "只有一句"
    with pytest.raises(DrafterOutputError, match="auditor_asks"):
        _draft_full(json.dumps(payload, ensure_ascii=False))


def test_full_drafter_still_rejects_dict_evidence():
    payload = json.loads(FULL)
    payload["evidence"] = {"1": "a", "2": "b", "3": "c"}
    with pytest.raises(DrafterOutputError, match="evidence"):
        _draft_full(json.dumps(payload, ensure_ascii=False))


def test_full_drafter_rejects_a_missing_differentiating_field():
    payload = json.loads(FULL)
    del payload["common_myth"]
    with pytest.raises(DrafterOutputError, match="common_myth"):
        _draft_full(json.dumps(payload, ensure_ascii=False))


def test_repairs_raw_newline_inside_a_string():
    """实测：DeepSeek 会在字符串值里直接换行，JSON 非法。

    转义换行是**纯语法修复，不改内容**——与「不许自动修复」那条规矩不冲突：
    那条禁的是替模型补内容，这里一个字都没动。
    """
    from framework_reader.interpret.drafter import parse_json_object

    broken = '{"intent": "第一行\n第二行", "x": 1}'
    assert parse_json_object(broken)["intent"] == "第一行\n第二行"


def test_repairs_trailing_comma():
    from framework_reader.interpret.drafter import parse_json_object

    assert parse_json_object('{"a": 1, "b": [1, 2,], }') == {"a": 1, "b": [1, 2]}


def test_does_not_touch_escaped_sequences_inside_strings():
    from framework_reader.interpret.drafter import parse_json_object

    assert parse_json_object(r'{"a": "已转义\n和引号\""}')["a"] == '已转义\n和引号"'


def test_commas_inside_strings_survive_repair():
    """修复只能动结构，不能动内容——字符串里的逗号和括号必须原样。"""
    from framework_reader.interpret.drafter import parse_json_object

    assert parse_json_object('{"a": "逗号, 和括号] 都要留着"}')["a"] == "逗号, 和括号] 都要留着"


def test_genuinely_broken_json_still_fails():
    from framework_reader.interpret.drafter import parse_json_object

    with pytest.raises(DrafterOutputError):
        parse_json_object('{"a": "引号不配对\', "b": 2}')
