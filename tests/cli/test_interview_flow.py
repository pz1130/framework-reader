"""fr interview 的编排链，端到端跑一遍（假模型、假编辑器、假时钟）。

W3 要跑 106 遍：registry → guard → 会话循环 → 抽取 → 写注释 → 拉编辑器 →
回读 → 签字 → 存盘。链上任何一处炸都是一次访谈的工时。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from framework_reader.cli.interview import run_interview
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
    fields_digest,
)
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


def _draft(store: InterpretationStore) -> None:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    store.save(Interpretation(control_id=CID, fields=fields))


def _session(store: InterpretationStore) -> InterviewSession:
    return InterviewSession(
        store, FakeClient([ADAPTIVE]), FakeClient([EXTRACTED]),
        outcome_lookup=lambda cid: "Access permissions are defined",
        questioner_model="q", extractor_model="x",
        extractor_provider="deepseek", extractor_prompt_version="2026.08-x1",
    )


def _run(tmp_path, *, answers=("答一", "答二", "答三"), edit=None, ticks=(0.0, 900.0)):
    store = InterpretationStore(tmp_path)
    _draft(store)
    asked: list[str] = []
    clock = iter(ticks)

    def ask(question) -> str:
        asked.append(question.text)
        return answers[question.n - 1]

    return store, asked, run_interview(
        store, _session(store), CID,
        ask=ask,
        edit=edit or (lambda path: None),
        signer="jc",
        now=lambda: datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc),
        clock=lambda: next(clock),
        threshold=0.35,
    )


def test_asks_three_questions_in_order(tmp_path):
    _, asked, _ = _run(tmp_path)
    assert len(asked) == 3
    assert asked[2] == "欧洲会更严吗？"


def test_ends_confirmed_and_signed(tmp_path):
    store, _, _ = _run(tmp_path)
    final = store.load(CID)
    assert final.state is InterpretationState.CONFIRMED
    assert final.provenance.confirmed_by == "jc"
    assert final.provenance.signed_digest == fields_digest(final)


def test_extracted_fields_land_in_the_file(tmp_path):
    store, _, _ = _run(tmp_path)
    final = store.load(CID)
    assert final.fields["common_myth"].value == "以为有张权限表就行"
    assert final.fields["common_myth"].basis is Basis.PRACTITIONER
    assert final.fields["regional_note"].value is None


def test_verbatim_answers_are_kept(tmp_path):
    store, _, _ = _run(tmp_path)
    assert [r.text for r in store.load(CID).interview.raw] == ["答一", "答二", "答三"]


def test_editor_sees_the_authors_own_words_as_comments(tmp_path):
    seen: list[str] = []
    _run(tmp_path, edit=lambda path: seen.append(Path(path).read_text(encoding="utf-8")))
    assert "# You said:" in seen[0]
    assert "答一" in seen[0]


def test_edits_made_in_the_editor_are_kept_and_signed(tmp_path):
    """作者在编辑器里改过的内容必须进最终文件，且签字覆盖的是改后的内容。"""
    def edit(path: Path) -> None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        data["fields"]["common_myth"]["value"] = "作者手改过的说法"
        Path(path).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    store, _, _ = _run(tmp_path, edit=edit)
    final = store.load(CID)
    assert final.fields["common_myth"].value == "作者手改过的说法"
    assert final.provenance.signed_digest == fields_digest(final)


def test_elapsed_seconds_are_recorded(tmp_path):
    """W2 spec §8.2：每条耗时是决定 W3 范围的那个数字，必须自动落账。"""
    store, _, elapsed = _run(tmp_path, ticks=(0.0, 900.0))
    assert elapsed == pytest.approx(900.0)
    assert store.load(CID).provenance.interview_seconds == pytest.approx(900.0)


def test_answers_survive_an_abort_midway(tmp_path):
    store = InterpretationStore(tmp_path)
    _draft(store)

    def ask(question):
        if question.n == 2:
            raise KeyboardInterrupt
        return "答一"

    with pytest.raises(KeyboardInterrupt):
        run_interview(
            store, _session(store), CID, ask=ask, edit=lambda p: None, signer="jc",
            now=lambda: datetime.now(timezone.utc), clock=lambda: 0.0, threshold=0.35,
        )
    assert [r.text for r in store.load(CID).interview.raw] == ["答一"]
    assert store.load(CID).state is InterpretationState.DRAFT
