import json

import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
)
from framework_reader.interpret.questioner import Q1_TEXT, Q2_TEXT
from framework_reader.interpret.session import InterviewSession
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import FakeClient

CID = "NIST-CSF-2.0:PR.AA-05"
ADAPTIVE = json.dumps({"question": "欧洲会更严吗？"}, ensure_ascii=False)
EXTRACTED = json.dumps({
    "common_myth": "以为有张权限表就行",
    "auditor_asks": ["上次复核谁签的字"],
    "regional_note": None,
}, ensure_ascii=False)


def _draft_file(store: InterpretationStore) -> None:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    store.save(Interpretation(control_id=CID, fields=fields))


def _session(tmp_path, questioner=None, extractor=None) -> tuple:
    store = InterpretationStore(tmp_path)
    _draft_file(store)
    session = InterviewSession(
        store,
        questioner or FakeClient([ADAPTIVE]),
        extractor or FakeClient([EXTRACTED]),
        outcome_lookup=lambda cid: "Access permissions are defined and reviewed",
        questioner_model="q", extractor_model="x",
        extractor_provider="deepseek", extractor_prompt_version="2026.08-x1",
    )
    return store, session


def test_first_question_is_fixed_q1(tmp_path):
    _, session = _session(tmp_path)
    assert session.next_question(CID).text == Q1_TEXT


def test_second_question_is_fixed_q2_and_costs_no_model_call(tmp_path):
    questioner = FakeClient([])          # 空队列：调一次就炸
    store, session = _session(tmp_path, questioner=questioner)
    session.record(CID, 1, "以为有张权限表就行")
    assert session.next_question(CID).text == Q2_TEXT
    assert questioner.calls == []


def test_third_question_is_adaptive(tmp_path):
    _, session = _session(tmp_path)
    session.record(CID, 1, "以为有张权限表就行")
    session.record(CID, 2, "上次复核谁签的字")
    q = session.next_question(CID)
    assert (q.n, q.kind, q.text) == (3, "adaptive", "欧洲会更严吗？")


def test_adaptive_question_gets_the_framework_text_not_the_draft(tmp_path):
    """提问器要看框架原文；喂 intent 初稿等于让模型顺着自己的话往下问。"""
    questioner = FakeClient([ADAPTIVE])
    _, session = _session(tmp_path, questioner=questioner)
    session.record(CID, 1, "答一")
    session.record(CID, 2, "答二")
    session.next_question(CID)
    sent = questioner.calls[0]["messages"][0]["content"]
    assert "Access permissions are defined and reviewed" in sent
    assert "草稿" not in sent


def test_no_more_questions_after_three_answers(tmp_path):
    _, session = _session(tmp_path)
    for n, text in ((1, "a"), (2, "b"), (3, "c")):
        session.record(CID, n, text)
    assert session.next_question(CID) is None


def test_answer_is_persisted_immediately(tmp_path):
    """答完一问就落盘——这是 W2 spec §6 的主线。"""
    store, session = _session(tmp_path)
    session.record(CID, 1, "以为有张权限表就行")
    assert [r.text for r in store.load(CID).interview.raw] == ["以为有张权限表就行"]


def test_resume_picks_up_where_it_stopped(tmp_path):
    """崩了重进，从第 2 问继续，不重问第 1 问。"""
    store, session = _session(tmp_path)
    session.record(CID, 1, "第一答")
    fresh = InterviewSession(
        store, FakeClient([ADAPTIVE]), FakeClient([EXTRACTED]),
        outcome_lookup=lambda cid: "Access permissions are defined and reviewed",
        questioner_model="q", extractor_model="x",
        extractor_provider="deepseek", extractor_prompt_version="v",
    )
    assert fresh.next_question(CID).text == Q2_TEXT


def test_finish_runs_extraction_and_moves_to_interviewed(tmp_path):
    store, session = _session(tmp_path)
    for n, text in ((1, "以为有张权限表就行"), (2, "上次复核谁签的字"), (3, "没差别")):
        session.record(CID, n, text)
    session.next_question(CID)
    result = session.finish(CID)
    assert result.state is InterpretationState.INTERVIEWED
    assert result.fields["common_myth"].value == "以为有张权限表就行"
    assert result.fields["common_myth"].basis is Basis.PRACTITIONER


def test_finish_records_extractor_provenance(tmp_path):
    store, session = _session(tmp_path)
    for n in (1, 2, 3):
        session.record(CID, n, f"答{n}")
    session.next_question(CID)
    ref = session.finish(CID).provenance.extractor
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "deepseek", "x", "2026.08-x1"
    )


def test_finish_before_three_answers_is_refused(tmp_path):
    _, session = _session(tmp_path)
    session.record(CID, 1, "只答了一问")
    with pytest.raises(ValueError, match="three questions"):
        session.finish(CID)


def test_finish_refuses_reextract_on_interviewed_without_force(tmp_path):
    store, session = _session(
        tmp_path, extractor=FakeClient([EXTRACTED, EXTRACTED])
    )
    for n, text in ((1, "以为有张权限表就行"), (2, "上次复核谁签的字"), (3, "没差别")):
        session.record(CID, n, text)
    session.next_question(CID)
    session.finish(CID)
    with pytest.raises(ValueError, match="interviewed"):
        session.finish(CID)
    again = session.finish(CID, force=True)
    assert again.state is InterpretationState.INTERVIEWED


def test_drafted_fields_survive_the_interview(tmp_path):
    store, session = _session(tmp_path)
    for n in (1, 2, 3):
        session.record(CID, n, f"答{n}")
    session.next_question(CID)
    result = session.finish(CID)
    assert result.fields["intent"].value == "草稿"
    assert result.fields["intent"].basis is Basis.INFERRED


def test_finish_records_answers_that_landed_nowhere(tmp_path):
    """落不进字段可以，静默消失不行。实测 DE.CM-01 丢了整条答 3。"""
    extractor = FakeClient([json.dumps({
        "common_myth": "以为有张权限表就行",
        "auditor_asks": None,
        "regional_note": None,
    }, ensure_ascii=False)])
    store, session = _session(tmp_path, extractor=extractor)
    session.record(CID, 1, "以为有张权限表就行")
    session.record(CID, 2, "答二")
    session.record(CID, 3, "接入siem，写usecase")
    session.next_question(CID)
    result = session.finish(CID)
    assert result.interview.unplaced == [2, 3]
