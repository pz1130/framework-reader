"""用户帮 AI 一起解读。2026-08-23 决

模型起草的是初稿，用户的经验才是这份材料值钱的地方。两件事让它们互相帮上忙：

1. **只补空格**——用户写过的一概不动，AI 只写空着的。否则那条控制会因为
   「已存在解读」被整条跳过，六个空字段永远空着。
2. **拿已确认的当范例**——他确认过的条款就是他公司的口径与颗粒度。
   模型学它比学 CSF 的黄金样例更贴。
"""
import json
import sqlite3

import pytest

from framework_reader.interpret.batch import draft_all, own_examples
from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
)
from framework_reader.interpret.user_store import UserInterpretationStore
from framework_reader.llm.client import FakeClient
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, LicenseTier

FULL_JSON = json.dumps({
    "intent": "模型写的意图", "plain_zh": "模型写的大白话",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "模型写的证据",
    "common_myth": "模型写的误解", "auditor_asks": ["模型写的追问"],
    "regional_note": None,
}, ensure_ascii=False)

FID = "ACME-SEC-2026"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    conn.close()

    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id=FID, name="ACME 制度",
        controls=[
            ("4.1", "日志留存", None, "留存不少于六个月。"),
            ("4.2", "口令强度", None, "口令至少 12 位。"),
        ],
    )
    return path


def _run(db, store, *, fill_blanks=False, client=None, **kw):
    return draft_all(
        store, QueryAPI(db), client or FakeClient([FULL_JSON, FULL_JSON, FULL_JSON]),
        framework_id=FID, model="m", prompt_version="v", provider="p",
        jobs=1, full=True, fill_blanks=fill_blanks, **kw,
    )


def _partial(control_id: str, **written) -> Interpretation:
    """一条只写了几个字段的解读，其余空着。"""
    return Interpretation(
        control_id=control_id,
        fields={
            n: Field(
                value=written.get(n),
                basis=Basis.PRACTITIONER if written.get(n) else Basis.INFERRED,
            )
            for n in ALL_FIELDS
        },
    )


# ---------- 只补空格 ----------

def test_a_control_with_blanks_is_not_skipped(db):
    """老行为：只要存在解读就整条跳过。用户写了一句，其余六个字段就永远空着。"""
    store = UserInterpretationStore()
    store.save(_partial(f"{FID}:4.1", intent="我写的意图"))
    report = _run(db, store, fill_blanks=True)
    assert f"{FID}:4.1" in report.written


def test_what_you_wrote_survives(db):
    store = UserInterpretationStore()
    store.save(_partial(f"{FID}:4.1", intent="我写的意图"))
    _run(db, store, fill_blanks=True)
    field = store.load(f"{FID}:4.1").fields["intent"]
    assert field.value == "我写的意图" and field.basis is Basis.PRACTITIONER


def test_the_blanks_do_get_filled(db):
    store = UserInterpretationStore()
    store.save(_partial(f"{FID}:4.1", intent="我写的意图"))
    _run(db, store, fill_blanks=True)
    assert store.load(f"{FID}:4.1").fields["plain_zh"].value == "模型写的大白话"


def test_an_ai_field_you_kept_is_also_left_alone(db):
    """「补空缺」的意思是别碰我看过的那些，包括我看过并认可的 AI 初稿。"""
    store = UserInterpretationStore()
    interp = _partial(f"{FID}:4.1", intent="我写的意图")
    interp.fields["evidence"] = Field(value="上一版 AI 写的证据", basis=Basis.INFERRED)
    store.save(interp)
    _run(db, store, fill_blanks=True)
    assert store.load(f"{FID}:4.1").fields["evidence"].value == "上一版 AI 写的证据"


def test_a_control_with_nothing_blank_is_skipped(db):
    """七个字段都有字了就别再花钱。"""
    store = UserInterpretationStore()
    store.save(Interpretation(
        control_id=f"{FID}:4.1",
        fields={n: Field(value="有字", basis=Basis.INFERRED) for n in ALL_FIELDS},
    ))
    report = _run(db, store, fill_blanks=True, only=[f"{FID}:4.1"])
    assert report.written == []


def test_without_fill_blanks_the_old_behaviour_stands(db):
    """整框架起草仍然只碰没有解读的条款，不会把已有的重跑一遍花钱。"""
    store = UserInterpretationStore()
    store.save(_partial(f"{FID}:4.1", intent="我写的意图"))
    report = _run(db, store, only=[f"{FID}:4.1"])
    assert report.written == []


def test_one_control_at_a_time(db):
    """单条起草：用户点某一条的「补空缺」，不该把整个框架都跑一遍。"""
    store = UserInterpretationStore()
    report = _run(db, store, fill_blanks=True, only=[f"{FID}:4.1"])
    assert report.written == [f"{FID}:4.1"]


# ---------- 拿已确认的当范例 ----------

def _confirmed(control_id: str, intent: str) -> Interpretation:
    from datetime import datetime, timezone

    return Interpretation(
        control_id=control_id,
        state=InterpretationState.CONFIRMED,
        fields={
            n: Field(
                value=(intent if n == "intent"
                       else {"1": "一", "2": "二", "3": "三"} if n == "practice"
                       else "别的字段"),
                basis=Basis.PRACTITIONER,
            )
            for n in ALL_FIELDS
        },
        provenance=InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)),
    )


def test_a_confirmed_control_becomes_an_example(db):
    store = UserInterpretationStore()
    store.save(_confirmed(f"{FID}:4.2", "我们这儿管这叫账号纪律"))
    examples = own_examples(store, FID, exclude=f"{FID}:4.1")
    assert [e.control_id for e in examples] == [f"{FID}:4.2"]


def test_an_unconfirmed_control_is_not_an_example(db):
    """没确认的那份可能本来就是模型写的。拿它当范例是让模型学自己。"""
    store = UserInterpretationStore()
    store.save(_partial(f"{FID}:4.2", intent="模型初稿，没人认领"))
    assert own_examples(store, FID, exclude=f"{FID}:4.1") == []


def test_the_control_being_drafted_is_never_its_own_example(db):
    store = UserInterpretationStore()
    store.save(_confirmed(f"{FID}:4.1", "自己"))
    assert own_examples(store, FID, exclude=f"{FID}:4.1") == []


def test_another_frameworks_confirmed_control_is_not_borrowed(db):
    store = UserInterpretationStore()
    store.save(_confirmed("OTHER:1", "别家的口径"))
    assert own_examples(store, FID, exclude=f"{FID}:4.1") == []


def test_your_wording_reaches_the_prompt(db):
    """范例最终要出现在发给模型的 payload 里，否则这个功能是摆设。"""
    store = UserInterpretationStore()
    store.save(_confirmed(f"{FID}:4.2", "我们这儿管这叫账号纪律"))
    client = FakeClient([FULL_JSON])
    _run(db, store, fill_blanks=True, only=[f"{FID}:4.1"], client=client)
    sent = "\n".join(call["system"] for call in client.calls)
    assert "我们这儿管这叫账号纪律" in sent


def test_golden_examples_top_up_when_you_have_too_few(db):
    """本组织的范例不足三条时用手写黄金样例补齐，不是干脆不给范例。"""
    from framework_reader.interpret.batch import _examples_for

    store = UserInterpretationStore()
    store.save(_confirmed(f"{FID}:4.2", "我们这儿管这叫账号纪律"))
    examples = _examples_for(store, FID, f"{FID}:4.1", own=True)
    assert examples[0].control_id == f"{FID}:4.2"
    assert len(examples) >= 1
