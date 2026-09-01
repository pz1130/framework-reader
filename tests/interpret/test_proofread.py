"""校对 pass：只改语言，不改内容。改动必须可自动分类，否则等于放任模型改写。"""
import json

import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
)
from framework_reader.interpret.proofread import (
    classify_edit,
    proofread_fields,
)
from framework_reader.llm.client import FakeClient


def _fields(**over) -> dict[str, Field]:
    base = {n: Field(value="原文", basis=Basis.INFERRED) for n in ALL_FIELDS}
    base["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    base["auditor_asks"] = Field(value=["问一"], basis=Basis.INFERRED)
    base["regional_note"] = Field(value=None, basis=Basis.INFERRED)
    base.update(over)
    return base


# ---------- 改动分类 ----------

def test_pure_language_fix_is_accepted():
    v = classify_edit("只落在工人脑子里", "只落在工程师脑子里")
    assert v.ok and v.reason == ""


def test_quote_normalisation_is_accepted():
    assert classify_edit('以为“有发言人就行”', "以为「有发言人就行」").ok


def test_dropping_a_number_is_flagged():
    """事实性内容不许在校对里消失。"""
    v = classify_edit("预算每年涨了10%就算配了资源", "预算涨了就算配了资源")
    assert not v.ok and "10" in v.reason


def test_adding_an_entity_is_flagged():
    v = classify_edit("欧盟审计员更看重职责分离", "欧盟审计员依据 NIS2 更看重职责分离")
    assert not v.ok and "NIS2" in v.reason


def test_wholesale_rewrite_is_flagged():
    v = classify_edit(
        "以为有权限矩阵挂在墙上就达标，实际权限与矩阵往往不一致",
        "权限管理应当健全并定期评估",
    )
    assert not v.ok and "Overlap" in v.reason


def test_unchanged_text_is_accepted():
    assert classify_edit("一模一样", "一模一样").ok


def test_empty_stays_empty_is_accepted():
    assert classify_edit("", "").ok


def test_deleting_all_content_is_flagged():
    assert not classify_edit("有内容的一句话", "").ok


# ---------- 校对本身 ----------

# 真实的校对长这样：只换一个语域错词，其余一字不动。
BEFORE_INTENT = "调查动作只落在工人脑子里，事后无从追溯"
AFTER_INTENT = "调查动作只落在工程师脑子里，事后无从追溯"

GOOD = json.dumps({
    "intent": AFTER_INTENT, "plain_zh": "原文",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "原文",
    "common_myth": "原文", "auditor_asks": ["问一"], "regional_note": None,
}, ensure_ascii=False)


def _before() -> dict[str, Field]:
    return _fields(intent=Field(value=BEFORE_INTENT, basis=Basis.INFERRED))


def test_proofread_returns_all_seven_fields():
    fields, _ = proofread_fields(
        FakeClient([GOOD]), control_id="X:1", fields=_before(), model="m"
    )
    assert set(fields) == set(ALL_FIELDS)


def test_proofread_keeps_basis_untouched():
    """校对只动文字，provenance 不因它改变。"""
    fields, _ = proofread_fields(
        FakeClient([GOOD]), control_id="X:1", fields=_before(), model="m"
    )
    assert all(f.basis is Basis.INFERRED for f in fields.values())


def test_author_written_fields_are_never_proofread():
    """作者亲手写的字段一个字都不许模型碰。"""
    before = _before()
    before["common_myth"] = Field(value="作者原话", basis=Basis.PRACTITIONER)
    payload = json.loads(GOOD)
    payload["common_myth"] = "模型想改的版本"
    fields, _ = proofread_fields(
        FakeClient([json.dumps(payload, ensure_ascii=False)]),
        control_id="X:1", fields=before, model="m",
    )
    assert fields["common_myth"].value == "作者原话"
    assert fields["common_myth"].basis is Basis.PRACTITIONER


def test_flagged_change_is_not_applied_but_reported():
    payload = json.loads(GOOD)
    payload["intent"] = "彻底换一句毫不相干的话"
    fields, flags = proofread_fields(
        FakeClient([json.dumps(payload, ensure_ascii=False)]),
        control_id="X:1", fields=_before(), model="m",
    )
    assert fields["intent"].value == BEFORE_INTENT, "可疑改动不得自动落盘"
    assert [f.field for f in flags] == ["intent"]
    assert flags[0].before == BEFORE_INTENT


def test_accepted_change_is_applied():
    """只换一个语域错词——这才是校对该做的事，应当自动落盘。"""
    fields, flags = proofread_fields(
        FakeClient([GOOD]), control_id="X:1", fields=_before(), model="m"
    )
    assert fields["intent"].value == AFTER_INTENT
    assert flags == []


# ---------- 语域漂移 ----------

def test_colloquial_to_formal_drift_is_flagged():
    """实测 p2：校对把「审批记录在哪？」改成「在何处？」——没动内容，但把话改僵了。

    auditor_asks 的价值就在于它像人说出口的话，书面化等于抹掉卖点。
    """
    v = classify_edit("这次公告是谁批准的？审批记录在哪？",
                      "这次公告是谁批准的？审批记录在何处？")
    assert not v.ok and "Register drift" in v.reason


def test_several_drift_patterns_are_caught():
    for before, after in (
        ("客户问恢复时间", "客户询问恢复时间"),
        ("没人拦", "无人拦截"),
        ("怎么发现的", "如何发现的"),
    ):
        assert not classify_edit(before, after).ok, (before, after)


def test_fixing_a_genuine_error_is_still_accepted():
    """语域检查不能把真正的修病句一起拦掉。"""
    assert classify_edit("只落在工人脑子里", "只落在工程师脑子里").ok
    assert classify_edit("明确了指定对外发言人", "明确指定对外发言人").ok
