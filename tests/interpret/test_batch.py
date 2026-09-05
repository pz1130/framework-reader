import json
import sqlite3

import pytest

from framework_reader.interpret.batch import draft_all
from framework_reader.interpret.model import Basis, DRAFTED_FIELDS, InterpretationState
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import FakeClient
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

DRAFT_JSON = json.dumps({
    "intent": "意图", "plain_zh": "大白话",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "证据",
}, ensure_ascii=False)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM", framework_id="NIST-CSF-2.0",
                         label="Continuous Monitoring", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-02", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="The physical environment is monitored",
                         label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    conn.close()
    return path


def test_writes_one_draft_file_per_leaf_control(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    report = draft_all(
        store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
        framework_id="NIST-CSF-2.0", model="m",
        prompt_version="2026.08-d1", provider="anthropic", jobs=1,
    )
    assert report.written == ["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02"]
    interp = store.load("NIST-CSF-2.0:DE.CM-01")
    assert interp.state is InterpretationState.DRAFT
    assert set(DRAFTED_FIELDS) <= set(interp.fields)


def test_differentiating_fields_start_empty_and_practitioner_sourced(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="2026.08-d1", provider="anthropic", jobs=1)
    interp = store.load("NIST-CSF-2.0:DE.CM-01")
    for name in ("common_myth", "auditor_asks", "regional_note"):
        assert interp.fields[name].value is None
        assert interp.fields[name].basis is Basis.PRACTITIONER


def test_records_drafter_provenance(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="claude-opus-5",
              prompt_version="2026.08-d1", provider="anthropic", jobs=1)
    ref = store.load("NIST-CSF-2.0:DE.CM-01").provenance.drafter
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "anthropic", "claude-opus-5", "2026.08-d1"
    )


def test_existing_files_are_skipped_without_force(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="v", provider="p", jobs=1)
    again = draft_all(store, QueryAPI(db), FakeClient([]),
                      framework_id="NIST-CSF-2.0", model="m",
                      prompt_version="v", provider="p", jobs=1)
    assert again.written == [], "已有文件不得被重跑覆盖——作者的访谈内容会丢"


def test_force_never_erases_the_authors_words(tmp_path, db):
    """作者原话永不因 --force 起草被抹掉。W2 spec §6

    实现从「整个操作罢工」改成了「逐字段保留」——目的没变，粒度变细了：
    106 条里只要有一条有 raw 就没法迭代提示词，那道闸挡的是自己人。
    """
    from framework_reader.interpret.model import RawAnswer

    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="v", provider="p", jobs=1)
    cid = "NIST-CSF-2.0:DE.CM-01"
    interp = store.load(cid)
    interp.interview.raw = [RawAnswer(n=1, text="作者原话不得丢")]
    store.save(interp)

    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="v", provider="p", jobs=1, force=True)
    assert store.load(cid).interview.raw[0].text == "作者原话不得丢"


def test_force_redrafts_when_raw_empty(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON, DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="v", provider="p", jobs=1)
    rewritten = draft_all(
        store, QueryAPI(db), FakeClient([DRAFT_JSON]),
        framework_id="NIST-CSF-2.0", model="m2",
        prompt_version="v2", provider="p2", jobs=1, force=True,
    )
    assert rewritten.written == ["NIST-CSF-2.0:DE.CM-01"]
    ref = store.load("NIST-CSF-2.0:DE.CM-01").provenance.drafter
    assert (ref.provider, ref.model, ref.prompt_version) == ("p2", "m2", "v2")


@pytest.fixture
def db_two_leaves(tmp_path):
    """Two active leaves so jobs>1 actually fans out across workers."""
    path = tmp_path / "c2.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM", framework_id="NIST-CSF-2.0",
                         label="Continuous Monitoring", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-02", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="The physical environment is monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    conn.close()
    return path


def test_parallel_jobs_open_per_worker_query_api(tmp_path, db_two_leaves):
    """jobs>1 must not share one sqlite connection across threads."""
    store = InterpretationStore(tmp_path / "interp")
    written = draft_all(
        store, QueryAPI(db_two_leaves), FakeClient([DRAFT_JSON, DRAFT_JSON]),
        framework_id="NIST-CSF-2.0", model="m",
        prompt_version="2026.08-d1", provider="anthropic", jobs=2,
    )
    assert written.written == ["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02"]
    for cid in written.written:
        assert store.load(cid).state is InterpretationState.DRAFT


def test_only_limits_drafting_to_the_named_controls(tmp_path, db):
    """先起草要用的那几条，不必为 106 条付账——尤其是厂商还没定的时候。"""
    store = InterpretationStore(tmp_path / "interp")
    report = draft_all(
        store, QueryAPI(db), FakeClient([DRAFT_JSON]),
        framework_id="NIST-CSF-2.0", model="m",
        prompt_version="v", provider="p", jobs=1,
        only=["NIST-CSF-2.0:DE.CM-01"],
    )
    assert report.written == ["NIST-CSF-2.0:DE.CM-01"]


def test_only_with_an_unknown_control_fails_loudly(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    with pytest.raises(ValueError, match="NIST-CSF-2.0:NOPE"):
        draft_all(
            store, QueryAPI(db), FakeClient([]),
            framework_id="NIST-CSF-2.0", model="m",
            prompt_version="v", provider="p", jobs=1,
            only=["NIST-CSF-2.0:NOPE"],
        )


FULL_JSON = json.dumps({
    "intent": "意图", "plain_zh": "大白话",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "证据",
    "common_myth": "误解", "auditor_asks": ["追问"], "regional_note": None,
}, ensure_ascii=False)


def test_full_mode_writes_all_seven_fields_as_inferred(tmp_path, db):
    """B 路线：fr draft --full 一次写全七个字段，一律标 inferred。"""
    from framework_reader.interpret.model import ALL_FIELDS, Basis

    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([FULL_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="2026.08-f1", provider="minimax", jobs=1, full=True)
    interp = store.load("NIST-CSF-2.0:DE.CM-01")
    assert set(interp.fields) == set(ALL_FIELDS)
    assert interp.fields["common_myth"].value == "误解"
    assert all(f.basis is Basis.INFERRED for f in interp.fields.values())


def _existing_with_human_content(store, cid: str):
    """模拟：这条已经有作者原话，且有一个字段是作者亲手改过的。"""
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation, RawAnswer,
    )

    fields = {n: Field(value="旧的 AI 稿", basis=Basis.INFERRED) for n in ALL_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    fields["common_myth"] = Field(value="作者亲手写的误解", basis=Basis.PRACTITIONER)
    interp = Interpretation(control_id=cid, fields=fields)
    interp.interview.raw = [RawAnswer(n=1, text="以为部署了 IDS 就算做到监控了")]
    store.save(interp)


def test_regenerating_keeps_the_authors_words_and_edits(tmp_path, db):
    """重跑起草只能覆盖 AI 写的部分。作者的原话与他改过的字段一律保留。

    此前的实现是整个操作罢工（拒绝 --force），106 条里只要有一条有 raw
    就没法迭代提示词——闸的方向对，粒度错。
    """
    from framework_reader.interpret.model import Basis

    store = InterpretationStore(tmp_path / "interp")
    _existing_with_human_content(store, "NIST-CSF-2.0:DE.CM-01")
    draft_all(store, QueryAPI(db), FakeClient([FULL_JSON]),
              framework_id="NIST-CSF-2.0", model="m", prompt_version="2026.08-f1",
              provider="minimax", jobs=1, full=True, force=True)
    after = store.load("NIST-CSF-2.0:DE.CM-01")
    # 作者的东西原样还在
    assert [r.text for r in after.interview.raw] == ["以为部署了 IDS 就算做到监控了"]
    assert after.fields["common_myth"].value == "作者亲手写的误解"
    assert after.fields["common_myth"].basis is Basis.PRACTITIONER
    # AI 写的部分被换成新稿
    assert after.fields["intent"].value == "意图"
    assert after.fields["intent"].basis is Basis.INFERRED


def test_one_bad_control_does_not_abort_the_whole_batch(tmp_path, db):
    """106 条里有一条模型抽风，不能把另外 105 条一起拖下水。

    实测：跑到第 44 条时一条返回残缺 JSON，整批中断。
    """
    store = InterpretationStore(tmp_path / "interp")
    # 第一条好、第二条坏（缺字段）
    bad = json.dumps({"intent": "只有这一个字段"}, ensure_ascii=False)
    report = draft_all(
        store, QueryAPI(db), FakeClient([FULL_JSON, bad]),
        framework_id="NIST-CSF-2.0", model="m", prompt_version="v",
        provider="p", jobs=1, full=True,
        only=["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02"],
    )
    assert report.written == ["NIST-CSF-2.0:DE.CM-01"]
    assert [f.control_id for f in report.failed] == ["NIST-CSF-2.0:DE.CM-02"]
    assert "missing fields" in report.failed[0].reason


def test_report_is_truthful_when_everything_succeeds(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    report = draft_all(store, QueryAPI(db), FakeClient([FULL_JSON]),
                       framework_id="NIST-CSF-2.0", model="m", prompt_version="v",
                       provider="p", jobs=1, full=True,
                       only=["NIST-CSF-2.0:DE.CM-01"])
    assert report.failed == []
    assert len(report.written) == 1


def test_rerun_without_force_fills_only_the_gaps(tmp_path, db):
    """失败的那几条，不加 --force 重跑就会补上——已成功的不重花钱。"""
    store = InterpretationStore(tmp_path / "interp")
    bad = json.dumps({"intent": "残缺"}, ensure_ascii=False)
    draft_all(store, QueryAPI(db), FakeClient([FULL_JSON, bad]),
              framework_id="NIST-CSF-2.0", model="m", prompt_version="v",
              provider="p", jobs=1, full=True,
              only=["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02"])
    again = draft_all(store, QueryAPI(db), FakeClient([FULL_JSON]),
                      framework_id="NIST-CSF-2.0", model="m", prompt_version="v",
                      provider="p", jobs=1, full=True,
                      only=["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02"])
    assert again.written == ["NIST-CSF-2.0:DE.CM-02"]


def test_failure_dumps_the_raw_response_for_diagnosis(tmp_path, db):
    from framework_reader.interpret.drafter import DRAFT_FAILURE_DIR

    store = InterpretationStore(tmp_path / "interp")
    bad = json.dumps({"intent": "残缺"}, ensure_ascii=False)
    dump_dir = tmp_path / "dumps"
    draft_all(store, QueryAPI(db), FakeClient([bad]),
              framework_id="NIST-CSF-2.0", model="m", prompt_version="v",
              provider="p", jobs=1, full=True,
              only=["NIST-CSF-2.0:DE.CM-01"], failure_dir=dump_dir)
    dumps = list(dump_dir.glob("*.txt"))
    assert len(dumps) == 1 and "残缺" in dumps[0].read_text(encoding="utf-8")
    assert DRAFT_FAILURE_DIR.name == "draft_failures"
