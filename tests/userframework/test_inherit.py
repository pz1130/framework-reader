"""换版继承的复制与校验。

三个不跟随（计划 Global Constraints）：签字不跟随、访谈原文不跟随、
旧条款的解读不跟随（复制不是搬移）。加一组拒绝分支：新条款已有解读、
旧条款无解读、边上不存在这对组合。
"""
import sqlite3
from datetime import UTC, datetime

import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState, InterviewRecord, RawAnswer,
)
from framework_reader.interpret.user_store import UserInterpretationStore
from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_supersessions,
)
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import (
    Framework, FrameworkControl, LicenseTier, SupersedeRelation, Supersession,
)
from framework_reader.userframework.inherit import InheritDenied, inherit

OLD = "NIST-800-53-R5:AC-2.10"
NEW = "NIST-800-53-R5:AC-2"
UNRELATED = "NIST-800-53-R5:AC-6"


@pytest.fixture
def env(tmp_path):
    content = tmp_path / "content.sqlite"
    user_db = tmp_path / "user.sqlite"
    conn = sqlite3.connect(content)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-800-53-R5", name="SP 800-53 Rev.5", version="R5",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [
        FrameworkControl(id=OLD, framework_id="NIST-800-53-R5",
                         label="Account management | Manual account changes",
                         label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=NEW, framework_id="NIST-800-53-R5",
                         label="Account management", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=UNRELATED, framework_id="NIST-800-53-R5",
                         label="Access enforcement", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    insert_supersessions(conn, [Supersession(
        old_id=OLD, new_id=NEW,
        relation=SupersedeRelation.INCORPORATED_INTO)])
    conn.close()
    store = UserInterpretationStore(user_db)
    api = QueryAPI(content, user_db=user_db)
    return store, api


def _old_interp() -> Interpretation:
    """旧条款上一条签过字的解读：字段有值、访谈有原文、签字齐全。"""
    return Interpretation(
        control_id=OLD,
        state=InterpretationState.CONFIRMED,
        fields={**{n: Field(value=None, basis=Basis.INFERRED)
                   for n in ALL_FIELDS},
                "intent": Field(value="把账号审批写进制度", basis=Basis.PRACTITIONER)},
        interview=InterviewRecord(raw=[
            RawAnswer(n=1, kind="fixed", text="审批谁签字？", answer="部门经理"),
        ]),
        provenance=InterpretationProvenance(
            confirmed_by="jc",
            confirmed_at=datetime(2026, 8, 1, tzinfo=UTC),
            interview_seconds=95.5,
        ),
    )


def test_inherit_copies_fields_and_marks_the_source(env):
    store, api = env
    store.save(_old_interp())
    got = inherit(OLD, NEW, store, api)
    assert got.control_id == NEW
    assert got.state is InterpretationState.DRAFT
    assert got.fields["intent"].value == "把账号审批写进制度"
    assert got.fields["intent"].basis is Basis.PRACTITIONER
    assert got.provenance.inherited_from == OLD
    # 复制不是搬移：旧条款的解读原样保留
    assert store.load(OLD).state is InterpretationState.CONFIRMED
    assert store.load(OLD).fields["intent"].value == "把账号审批写进制度"


def test_signature_and_interview_do_not_follow(env):
    store, api = env
    store.save(_old_interp())
    got = inherit(OLD, NEW, store, api)
    assert got.provenance.confirmed_by is None
    assert got.provenance.confirmed_at is None
    assert got.provenance.signed_digest is None
    assert got.provenance.interview_seconds is None
    assert got.interview == InterviewRecord(), "访谈原文属于旧条款，不得跟随"


def test_refuses_when_the_new_control_already_has_one(env):
    store, api = env
    store.save(_old_interp())
    store.save(Interpretation(
        control_id=NEW, state=InterpretationState.DRAFT,
        fields={n: Field(value=None, basis=Basis.INFERRED) for n in ALL_FIELDS},
    ))
    with pytest.raises(InheritDenied, match="already has an interpretation"):
        inherit(OLD, NEW, store, api)


def test_refuses_when_the_old_control_has_none(env):
    store, api = env
    with pytest.raises(InheritDenied, match="has no interpretation"):
        inherit(OLD, NEW, store, api)


def test_refuses_a_pair_that_is_not_an_edge(env):
    store, api = env
    store.save(_old_interp())
    with pytest.raises(InheritDenied, match="supersession relation"):
        inherit(OLD, UNRELATED, store, api)
