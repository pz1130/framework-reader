import json
import sqlite3
from datetime import datetime, timezone

import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import create_schema, insert_interpretations
from framework_reader.pack.glossary import Glossary, GlossaryEntry
from framework_reader.pack.validate import (
    BuildAssertionError,
    assert_glossary_clean,
    assert_only_confirmed,
)


def _interp(state=InterpretationState.CONFIRMED, myth="以为有张权限表就行"):
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value=myth, basis=Basis.PRACTITIONER)
    provenance = InterpretationProvenance()
    if state is InterpretationState.CONFIRMED:
        provenance = InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        )
    return Interpretation(
        control_id="NIST-CSF-2.0:PR.AA-05", state=state,
        fields=fields, provenance=provenance,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    yield c
    c.close()


def test_interpretation_rows_are_one_per_field(conn):
    insert_interpretations(conn, [_interp()])
    names = [r[0] for r in conn.execute(
        "SELECT field FROM interpretation WHERE control_id = ? ORDER BY field",
        ("NIST-CSF-2.0:PR.AA-05",),
    )]
    assert len(names) == 7


def test_values_round_trip_through_json(conn):
    insert_interpretations(conn, [_interp()])
    row = conn.execute(
        "SELECT value_json, basis FROM interpretation WHERE field = 'common_myth'"
    ).fetchone()
    assert json.loads(row[0]) == "以为有张权限表就行"
    assert row[1] == "practitioner"


def test_locale_column_exists_from_day_one(conn):
    """主 spec §8⑤：locale 从第一天存在，即使当前只有 zh-CN。"""
    insert_interpretations(conn, [_interp()])
    assert conn.execute("SELECT DISTINCT locale FROM interpretation").fetchone()[0] == "zh-CN"


def test_unconfirmed_interpretation_fails_the_build():
    with pytest.raises(BuildAssertionError, match="draft"):
        assert_only_confirmed([_interp(state=InterpretationState.DRAFT)])


def test_confirmed_interpretations_pass():
    assert_only_confirmed([_interp()])


def test_glossary_violation_in_an_interpretation_fails_the_build():
    glossary = Glossary(entries=[GlossaryEntry(
        preferred="控制", banned=["控件"], en="control", rationale="统一术语"
    )])
    with pytest.raises(BuildAssertionError, match="控件"):
        assert_glossary_clean([_interp(myth="他们以为有个控件表就行")], glossary)


def test_clean_interpretations_pass_the_glossary(conn):
    glossary = Glossary(entries=[GlossaryEntry(
        preferred="控制", banned=["控件"], en="control", rationale="统一术语"
    )])
    assert_glossary_clean([_interp()], glossary)


# ---------- 草稿入包但标明成色（主 spec §7.3.1，2026-08-22 自用降级） ----------

def test_every_row_carries_the_state(conn):
    """草稿也进包，但成色必须写在包里——不能让读的人以为是定稿。"""
    insert_interpretations(conn, [_interp(state=InterpretationState.DRAFT)])
    states = {r[0] for r in conn.execute("SELECT state FROM interpretation")}
    assert states == {"draft"}


def test_confirmed_and_draft_can_coexist_in_one_pack(conn):
    draft = _interp(state=InterpretationState.DRAFT)
    draft.control_id = "NIST-CSF-2.0:GV.OC-01"
    insert_interpretations(conn, [_interp(), draft])
    rows = dict(conn.execute(
        "SELECT control_id, state FROM interpretation GROUP BY control_id"
    ))
    assert rows == {
        "NIST-CSF-2.0:PR.AA-05": "confirmed",
        "NIST-CSF-2.0:GV.OC-01": "draft",
    }
