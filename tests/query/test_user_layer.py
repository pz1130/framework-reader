"""用户导入的框架必须能被查询层看见。主 spec §6.1、§7.3.5

内容层只读、用户层可写的分离不动；查询层把两边**合起来看**——
否则用户导入的框架在浏览、自评、差距报告里全部不存在。
"""
import sqlite3
from pathlib import Path

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


@pytest.fixture
def content(tmp_path):
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0", label="Networks",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    return path


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.userframework.store import UserFrameworkStore

    store = UserFrameworkStore()
    store.add_framework(
        framework_id="ACME-SEC-2026", name="ACME 信息安全管理办法",
        controls=[("3.1", "账号管理", None, ""), ("3.2", "日志留存", None, "留存不少于六个月。")],
    )
    return store


def test_content_only_still_works(content):
    api = QueryAPI(content)
    assert api.get_control("NIST-CSF-2.0:DE.CM-01") is not None


def test_an_imported_framework_is_visible(content, user_db):
    api = QueryAPI(content)
    assert api.get_framework("ACME-SEC-2026").name == "ACME 信息安全管理办法"


def test_an_imported_control_is_visible(content, user_db):
    ctl = QueryAPI(content).get_control("ACME-SEC-2026:3.2")
    assert ctl is not None and ctl.label == "日志留存"


def test_imported_controls_list_under_their_framework(content, user_db):
    ids = [c.id for c in QueryAPI(content).list_controls("ACME-SEC-2026")]
    assert ids == ["ACME-SEC-2026:3.1", "ACME-SEC-2026:3.2"]


def test_builtin_frameworks_are_untouched_by_the_union(content, user_db):
    ids = [c.id for c in QueryAPI(content).list_controls("NIST-CSF-2.0")]
    assert ids == ["NIST-CSF-2.0:DE.CM-01"]


def test_search_reaches_imported_controls(content, user_db):
    assert [c.id for c in QueryAPI(content).search("日志留存")] == ["ACME-SEC-2026:3.2"]


def test_stats_count_both_layers(content, user_db):
    stats = QueryAPI(content).stats()
    assert stats["frameworks"] == 2
    assert stats["controls"] == 3


def test_the_content_pack_stays_read_only(content, user_db):
    """导入写的是用户库。内容包一个字节都不许动——它随时可以重建。"""
    before = content.read_bytes()
    QueryAPI(content).get_control("ACME-SEC-2026:3.1")
    assert content.read_bytes() == before


def test_a_missing_user_database_is_not_an_error(content, tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "empty-home"))
    assert QueryAPI(content).get_control("NIST-CSF-2.0:DE.CM-01") is not None


def test_imported_frameworks_are_listed(content, user_db):
    from framework_reader.userframework.store import UserFrameworkStore

    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-SEC-2026"]


def test_an_imported_framework_is_marked_never_redistributable(content, user_db):
    """用户导入的是他自己公司的东西。我们永远不分发它，tier 必须说出这件事。"""
    from framework_reader.schema.entities import LicenseTier

    assert QueryAPI(content).get_framework("ACME-SEC-2026").tier == LicenseTier.U_USER


def test_the_body_of_an_imported_control_is_readable(content, user_db):
    """起草导入的框架要用它——那是用户自己的制度原文。"""
    assert QueryAPI(content).control_body("ACME-SEC-2026:3.2") == "留存不少于六个月。"


def test_a_builtin_label_as_body_framework_reads_its_label(content, user_db):
    """CSF 2.0 的 subcategory 没有别的正文——官方 label 就是条款正文。

    起草一直靠 control_body 当依据，而它之前只查用户库：这 219 条
    的解读一直在按空正文编。官方 label 兑现后才有真依据。
    """
    assert QueryAPI(content).control_body("NIST-CSF-2.0:DE.CM-01") == "Networks"


def test_labels_that_are_not_bodies_stay_empty(content, user_db):
    """800-53 的 label 是控制标题，ISO 的是自写短标题——不能充正文。

    否则页面上「官方原文」拿标题充数，起草依据更误导。
    """
    path = content.parent / "other.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-800-53-R5", name="800-53", version="r5",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-800-53-R5:AC-2", framework_id="NIST-800-53-R5",
        label="Account Management", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    assert QueryAPI(path).control_body("NIST-800-53-R5:AC-2") == ""


def test_an_override_beats_the_official_label(content, user_db):
    """用户贴过的正文盖住官方 label——清空后官方那版回来。"""
    cid = "NIST-CSF-2.0:DE.CM-01"
    from framework_reader.userframework.store import UserFrameworkStore

    store = UserFrameworkStore()
    store.update_body(cid, "用户改过的正文")
    assert QueryAPI(content).control_body(cid) == "用户改过的正文"
    assert QueryAPI(content).body_is_official(cid) is False
    store.update_body(cid, "")
    assert QueryAPI(content).control_body(cid) == "Networks"
    assert QueryAPI(content).body_is_official(cid) is True


# ---------- 导入框架的解读 ----------

def _draft(control_id: str, intent: str):
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation,
    )

    return Interpretation(
        control_id=control_id,
        fields={
            name: Field(
                value=(intent if name == "intent"
                       else {"1": "一档", "2": "二档", "3": "三档"} if name == "practice"
                       else "x"),
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
    )


def test_a_drafted_interpretation_of_an_imported_control_is_readable(content, user_db):
    """起草跑完却查不到，等于没起草——这正是导入功能此前的死胡同。"""
    from framework_reader.interpret.user_store import UserInterpretationStore

    UserInterpretationStore().save(_draft("ACME-SEC-2026:3.2", "防的是日志被顺手清掉"))
    fields = QueryAPI(content).interpretation("ACME-SEC-2026:3.2")
    assert fields["intent"]["value"] == "防的是日志被顺手清掉"


def test_the_state_of_a_user_interpretation_is_readable(content, user_db):
    from framework_reader.interpret.user_store import UserInterpretationStore

    UserInterpretationStore().save(_draft("ACME-SEC-2026:3.2", "x"))
    assert QueryAPI(content).interpretation_state("ACME-SEC-2026:3.2") == "draft"


def test_an_undrafted_imported_control_has_no_interpretation(content, user_db):
    assert QueryAPI(content).interpretation("ACME-SEC-2026:3.1") == {}


def test_search_reaches_the_interpretation_of_an_imported_control(content, user_db):
    from framework_reader.interpret.user_store import UserInterpretationStore

    UserInterpretationStore().save(_draft("ACME-SEC-2026:3.2", "防的是日志被顺手清掉"))
    assert "ACME-SEC-2026:3.2" in [
        c.id for c in QueryAPI(content).search("顺手清掉")
    ]


def test_a_missing_user_database_still_reads_builtin_interpretations(
    content, tmp_path, monkeypatch
):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "empty-home"))
    assert QueryAPI(content).interpretation("NIST-CSF-2.0:DE.CM-01") == {}
