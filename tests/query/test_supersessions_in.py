"""按框架列出 supersession 边，两侧带解读状态——换版对照页的地基。

对照页要回答「这个框架里谁能继承谁」，所以一次取整条边：旧端、新端的
编号与标题，加上两侧解读成色（有解读才谈得上继承）。跨框架的边必须
排除——继承只在同一框架的换版里成立，张冠李戴比缺页更糟。
"""
import sqlite3

import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
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
    Framework,
    FrameworkControl,
    LicenseTier,
    SupersedeRelation,
    Supersession,
)

OLD = "NIST-800-53-R5:AC-2.10"
NEW = "NIST-800-53-R5:AC-2"
OTHER = "ISO-27002-2022:A.8.16"


@pytest.fixture
def dbs(tmp_path):
    content = tmp_path / "content.sqlite"
    user_db = tmp_path / "user.sqlite"
    conn = sqlite3.connect(content)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id="NIST-800-53-R5", name="SP 800-53 Rev.5", version="R5",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="ISO-27002-2022", name="ISO/IEC 27002:2022", version="2022",
                  tier=LicenseTier.C_PURCHASE, source_url="u", license_note="购买"),
    ])
    insert_controls(conn, [
        FrameworkControl(id=OLD, framework_id="NIST-800-53-R5",
                         label="Account management | Manual account changes",
                         label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=NEW, framework_id="NIST-800-53-R5",
                         label="Account management", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=OTHER, framework_id="ISO-27002-2022",
                         label="活动监控", label_is_original=False,
                         framework_tier=LicenseTier.C_PURCHASE),
    ])
    insert_supersessions(conn, [
        Supersession(old_id=OLD, new_id=NEW,
                     relation=SupersedeRelation.INCORPORATED_INTO),
        # 跨框架边：supersessions_in 必须把它挡在门外
        Supersession(old_id=OLD, new_id=OTHER,
                     relation=SupersedeRelation.INCORPORATED_INTO),
    ])
    conn.close()
    return content, user_db


def _draft(store, control_id, intent="旧条款上写好的解读"):
    store.save(Interpretation(
        control_id=control_id,
        state=InterpretationState.DRAFT,
        fields={**{n: Field(value=None, basis=Basis.INFERRED)
                   for n in ALL_FIELDS},
                "intent": Field(value=intent, basis=Basis.INFERRED)},
        provenance=InterpretationProvenance(),
    ))


def test_edges_come_with_both_sides_filled(dbs):
    content, user_db = dbs
    _draft(UserInterpretationStore(user_db), OLD)
    got = QueryAPI(content, user_db=user_db).supersessions_in("NIST-800-53-R5")
    assert len(got) == 1, "跨框架的边不得混进来"
    edge = got[0]
    assert (edge.old_id, edge.new_id) == (OLD, NEW)
    assert edge.old_label == "Account management | Manual account changes"
    assert edge.new_label == "Account management"
    assert edge.relation == "incorporated_into"
    assert edge.old_state == "draft", "旧端有解读，状态必须读得出来"
    assert edge.new_state is None, "新端没解读，不能瞎编一个状态"


def test_framework_without_edges_gives_an_empty_list(dbs):
    content, user_db = dbs
    assert QueryAPI(content, user_db=user_db).supersessions_in("ISO-27002-2022") == []


def test_without_a_user_db_the_states_are_none_not_a_crash(dbs):
    content, _ = dbs
    got = QueryAPI(content).supersessions_in("NIST-800-53-R5")
    assert len(got) == 1
    assert got[0].old_state is None
