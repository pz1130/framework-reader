from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from framework_reader.interpret.model import (
    ALL_FIELDS,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    InterviewRecord,
    ModelRef,
    Question,
    RawAnswer,
)


def _fields(**overrides: Field) -> dict[str, Field]:
    base = {name: Field(value="草稿", basis=Basis.INFERRED) for name in DRAFTED_FIELDS}
    base["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for name in DIFFERENTIATING_FIELDS:
        base[name] = Field(value=None, basis=Basis.PRACTITIONER)
    base["auditor_asks"] = Field(value=None, basis=Basis.PRACTITIONER)
    base.update(overrides)
    return base


def _interp(**kw) -> Interpretation:
    payload = dict(
        control_id="NIST-CSF-2.0:GV.SC-07",
        fields=_fields(),
        interview=InterviewRecord(),
        provenance=InterpretationProvenance(),
    )
    payload.update(kw)
    return Interpretation(**payload)


def test_seven_fields_exactly():
    """spec §3.4 的七个字段，不多不少。"""
    assert set(ALL_FIELDS) == set(DRAFTED_FIELDS) | set(DIFFERENTIATING_FIELDS)
    assert len(ALL_FIELDS) == 7
    assert set(DIFFERENTIATING_FIELDS) == {"common_myth", "auditor_asks", "regional_note"}


def test_locale_defaults_to_zh_cn():
    assert _interp().locale == "zh-CN"


def test_new_interpretation_starts_as_draft():
    assert _interp().state is InterpretationState.DRAFT


def test_empty_differentiating_field_is_allowed():
    """留空是信号，不是缺陷。W2 spec §2.2"""
    interp = _interp(fields=_fields(regional_note=Field(value=None, basis=Basis.PRACTITIONER)))
    assert interp.fields["regional_note"].value is None


def test_confirmed_requires_a_human_signature():
    with pytest.raises(ValidationError):
        _interp(state=InterpretationState.CONFIRMED)


def test_ai_may_not_sign():
    """主 spec §5：禁止直接落库。签字人不得是模型。"""
    with pytest.raises(ValidationError):
        _interp(
            state=InterpretationState.CONFIRMED,
            provenance=InterpretationProvenance(
                confirmed_by="ai:claude-opus-5",
                confirmed_at=datetime.now(timezone.utc),
            ),
        )


def test_confirmed_with_human_signature_is_valid():
    interp = _interp(
        state=InterpretationState.CONFIRMED,
        provenance=InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        ),
    )
    assert interp.state is InterpretationState.CONFIRMED


def test_missing_field_is_rejected():
    incomplete = _fields()
    del incomplete["evidence"]
    with pytest.raises(ValidationError):
        _interp(fields=incomplete)


def test_interview_record_holds_questions_and_verbatim_answers():
    record = InterviewRecord(
        questions=[Question(n=1, kind="fixed", text="最常见的误解是什么？")],
        raw=[RawAnswer(n=1, text="他们以为有张权限表就行")],
    )
    assert record.raw[0].text == "他们以为有张权限表就行"


def test_model_ref_records_provider_model_and_prompt_version():
    """换厂商等于换了生产条件，三样都要留痕。W2 spec §3.4②"""
    ref = ModelRef(provider="deepseek", model="deepseek-chat", prompt_version="2026.08-x1")
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "deepseek", "deepseek-chat", "2026.08-x1"
    )


def test_differentiating_fields_may_be_ai_written_under_route_b():
    """B 路线（2026-08-20）：七个字段全部由 AI 撰写，basis 一律 inferred。

    原先此处强制三个差异化字段必须是 practitioner——那是 D1 的不变式，D1 已推翻。
    """
    fields = _fields()
    for name in DIFFERENTIATING_FIELDS:
        fields[name] = Field(value="AI 写的", basis=Basis.INFERRED)
    assert _interp(fields=fields).fields["common_myth"].basis is Basis.INFERRED


def test_differentiating_fields_still_reject_quote_basis():
    """这三个字段不可能"依据原文某句"——原文里没有误解、追问、地域差异。

    标成 quote 说明建模错了，或者有人在伪造出处。
    """
    with pytest.raises(ValidationError):
        _interp(fields=_fields(common_myth=Field(value="x", basis=Basis.QUOTE)))
