"""用户改过的字段盖住内容包的——**逐字段**盖，不是整条盖。

合并视图原来是 UNION ALL：同一个字段两边都有时两行都回来，而
`interpretation()` 用 `{r["field"]: ...}` 收字典，留下哪一行取决于 SQL 的
返回顺序——没有定义。内置框架一旦能改，这就是个必然会踩的坑。

逐字段盖是关键：改了「怎么落地」不该把内容包里那六个字段一起顶掉。
"""
import sqlite3

import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CID = "NIST-CSF-2.0:DE.CM-01"


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    content = tmp_path / "content.sqlite"
    conn = sqlite3.connect(content)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=CID, framework_id="NIST-CSF-2.0", label="Networks monitored",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    insert_interpretations(conn, [Interpretation(
        control_id=CID, state=InterpretationState.DRAFT,
        fields={
            **{name: Field(value=None, basis=Basis.INFERRED)
               for name in ALL_FIELDS},
            "intent": Field(value="内容包写的意图", basis=Basis.INFERRED),
            "plain_zh": Field(value="内容包写的大白话", basis=Basis.INFERRED),
        },
        provenance=InterpretationProvenance())])
    conn.close()
    return content, tmp_path / "user.sqlite"


def _write(user_db, field, value):
    from framework_reader.interpret.authoring import write_field
    from framework_reader.interpret.user_store import UserInterpretationStore

    write_field(UserInterpretationStore(user_db), CID, field, value,
                basis=Basis.PRACTITIONER)


def test_without_a_user_edit_the_pack_shows(dbs):
    content, user_db = dbs
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["intent"]["value"] == "内容包写的意图"


def test_a_user_edit_wins_over_the_pack(dbs):
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["intent"]["value"] == "我改过的意图"


def test_the_other_fields_still_come_from_the_pack(dbs):
    """**逐字段盖。** 改了一个字段，不该把内容包里其余的一起顶掉。"""
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["plain_zh"]["value"] == "内容包写的大白话"


def test_who_wrote_it_follows_the_winning_layer(dbs):
    """盖过去的是人写的，出处也得跟着变——否则页面还标着「AI 初稿」。"""
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["intent"]["basis"] == "practitioner"
    assert got["plain_zh"]["basis"] == "inferred"


def test_a_field_only_the_user_has_shows_up(dbs):
    content, user_db = dbs
    _write(user_db, "evidence", "只有我写过这个")
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["evidence"]["value"] == "只有我写过这个"
    assert got["intent"]["value"] == "内容包写的意图"


def test_the_result_is_stable_across_calls(dbs):
    """UNION ALL 的老毛病：同一个字段两行都回来，留下哪一行看运气。"""
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    api = QueryAPI(content, user_db=user_db)
    assert {api.interpretation(CID)["intent"]["value"] for _ in range(20)} == {
        "我改过的意图"}


def test_one_field_per_name_comes_back(dbs):
    """两行都回来的时候，字典只留一行——另一行悄悄消失，而它可能才是对的。"""
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    api = QueryAPI(content, user_db=user_db)
    rows = api._conn.execute(
        "SELECT field, COUNT(*) n FROM all_interpretation "
        "WHERE control_id = ? GROUP BY field", (CID,)).fetchall()
    assert all(r["n"] == 1 for r in rows), "同一个字段回了不止一行"


def test_an_untouched_field_is_not_an_override(dbs):
    """`write_field` 一次写七个字段，没碰的那六个是 null。拿「有行」当判据，
    改一个字段会把内容包里其余六个全顶成空的。"""
    content, user_db = dbs
    _write(user_db, "evidence", "只有我写过这个")
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["intent"]["value"] == "内容包写的意图"
    assert got["plain_zh"]["value"] == "内容包写的大白话"


def test_clearing_a_builtin_field_falls_back_to_the_pack(dbs):
    """这是上面那条规则的另一面，写出来免得日后被当成 bug：
    把内置条款的某个字段清空，内容包那一版会回来——在内置条款上
    「清空」正好读作「恢复默认」。"""
    content, user_db = dbs
    _write(user_db, "intent", "我改过的意图")
    _write(user_db, "intent", None)
    got = QueryAPI(content, user_db=user_db).interpretation(CID)
    assert got["intent"]["value"] == "内容包写的意图"
