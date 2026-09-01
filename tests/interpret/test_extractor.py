import json

import pytest

from framework_reader.interpret.extractor import ExtractorOutputError, extract_fields
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import FakeClient

QUESTIONS = [
    Question(n=1, kind="fixed", text="最常见的误解是什么？"),
    Question(n=2, kind="fixed", text="审计员会追问哪几句？"),
    Question(n=3, kind="adaptive", text="欧洲会更严吗？"),
]
ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员会问上次复核是谁签的字，还会问离职当天权限什么时候收的"),
    RawAnswer(n=3, text="欧洲那边会追到具体的复核证据，美国更看流程写没写"),
]

GOOD = json.dumps({
    "common_myth": "他们以为有张权限矩阵表就算做到了",
    "auditor_asks": ["上次复核是谁签的字", "离职当天权限什么时候收的"],
    "regional_note": "欧洲那边会追到具体的复核证据，美国更看流程写没写",
}, ensure_ascii=False)


def _extract(response: str, tmp_path=None):
    return extract_fields(
        FakeClient([response]),
        control_id="NIST-CSF-2.0:PR.AA-05",
        questions=QUESTIONS,
        answers=ANSWERS,
        model="deepseek-chat",
        failure_dir=tmp_path,
    )


def test_produces_exactly_the_three_differentiating_fields(tmp_path):
    assert set(_extract(GOOD, tmp_path)) == set(DIFFERENTIATING_FIELDS)


def test_fields_are_marked_practitioner_sourced(tmp_path):
    """这三个字段的依据是作者的从业经验，不是原文也不是推断。W2 spec §2.4"""
    for field in _extract(GOOD, tmp_path).values():
        assert field.basis is Basis.PRACTITIONER


def test_null_field_is_allowed(tmp_path):
    """留空是信号。作者没料的字段不许模型补。W2 spec §2.2、§2.3"""
    payload = json.loads(GOOD)
    payload["regional_note"] = None
    fields = _extract(json.dumps(payload, ensure_ascii=False), tmp_path)
    assert fields["regional_note"].value is None


def test_all_raw_answers_and_questions_reach_the_prompt(tmp_path):
    client = FakeClient([GOOD])
    extract_fields(
        client, control_id="X:1", questions=QUESTIONS, answers=ANSWERS,
        model="m", failure_dir=tmp_path,
    )
    sent = client.calls[0]["messages"][0]["content"]
    for answer in ANSWERS:
        assert answer.text in sent
    for question in QUESTIONS:
        assert question.text in sent


def test_wrong_type_raises_instead_of_being_coerced(tmp_path):
    """auditor_asks 必须是列表。把字符串强转成 [字符串] 属于替模型收拾——不做。"""
    payload = json.loads(GOOD)
    payload["auditor_asks"] = "上次复核是谁签的字"
    with pytest.raises(ExtractorOutputError, match="auditor_asks"):
        _extract(json.dumps(payload, ensure_ascii=False), tmp_path)


def test_missing_key_raises(tmp_path):
    payload = json.loads(GOOD)
    del payload["common_myth"]
    with pytest.raises(ExtractorOutputError, match="common_myth"):
        _extract(json.dumps(payload, ensure_ascii=False), tmp_path)


def test_failure_dumps_the_raw_response_for_diagnosis(tmp_path):
    with pytest.raises(ExtractorOutputError):
        _extract("模型写了一段散文", tmp_path)
    dumps = list(tmp_path.glob("*.txt"))
    assert len(dumps) == 1
    assert "模型写了一段散文" in dumps[0].read_text(encoding="utf-8")


def test_failure_writes_nothing_but_the_dump(tmp_path):
    """抽取失败不得污染 content/——只留诊断文件。W2 spec §6"""
    with pytest.raises(ExtractorOutputError):
        _extract("坏输出", tmp_path)
    assert all(p.suffix == ".txt" for p in tmp_path.rglob("*") if p.is_file())
