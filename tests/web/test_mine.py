"""顶栏不再列框架，以及删框架。

框架列表本身在 `test_frameworks_page.py`——这一份只管顶栏和删除。

换框架走左上角「框架工作台」回首页。顶上摊一排方框，框架一多就折行，
每一页都跟着长胖。当前在哪个框架，看页面标题和面包屑，不靠那一排。
"""
import re
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier

BUILTIN = "NIST-CSF-2.0"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=BUILTIN, name="NIST Cybersecurity Framework 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db))


def _import(client, framework_id="ACME-1", name="ACME 制度"):
    return client.post(
        "/import",
        data={"framework_id": framework_id, "name": name},
        files={"file": ("f.csv", BytesIO("编号,标题\n3.1,账号管理\n".encode()),
                        "text/csv")},
        follow_redirects=False)


# ---------- 顶栏 ----------

def test_no_page_renders_the_framework_switcher(client):
    """那一排方框框架一多就折行。换框架回首页去点。"""
    _import(client)
    for path in ("/frameworks", "/import", f"/f/{BUILTIN}", "/f/ACME-1"):
        assert '<div class="tabs">' not in client.get(path).text, path


def test_every_page_can_get_home(client):
    _import(client)
    for path in ("/frameworks", "/import", f"/f/{BUILTIN}", "/f/ACME-1"):
        assert '<h1><a href="/">Framework Workbench</a></h1>' in client.get(path).text, path


def test_the_framework_you_are_looking_at_is_still_named(client):
    """顶栏不再列框架，但进了某个框架之后标题得说出你在哪儿。"""
    _import(client)
    page = client.get("/f/ACME-1").text
    assert "<h2>ACME 制度</h2>" in page
    assert "ACME-1" in page


# ---------- /mine ----------

def test_the_page_offers_a_way_to_delete(client):
    """FRAMEWORK_DELETE 这个权限一直定义着，但网页上没有任何入口——
    导错了的框架只能进数据库删。"""
    _import(client)
    assert "/f/ACME-1/delete" in client.get("/mine").text


def _text(html: str) -> str:
    """页面上人能读到的字。数字外面裹着 <strong>，按 HTML 片段断言会漏。"""
    import html as unescape_mod

    return unescape_mod.unescape(re.sub(r"<[^>]+>", "", html))


def test_the_confirm_page_says_exactly_what_will_be_destroyed(client):
    _import(client)
    page = _text(client.get("/f/ACME-1/delete").text)
    assert "1 controls" in page
    assert "self-assessments" in page
    assert "cannot be recovered" in page


def test_deleting_needs_the_id_typed_back(client):
    from framework_reader.userframework.store import UserFrameworkStore

    _import(client)
    client.post("/f/ACME-1/delete", data={"confirm": ""})
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]


def test_a_wrong_id_does_not_delete(client):
    from framework_reader.userframework.store import UserFrameworkStore

    _import(client)
    result = client.post("/f/ACME-1/delete", data={"confirm": "ACME-2"})
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]
    assert "does not match" in result.text


def test_the_right_id_deletes_it(client):
    from framework_reader.userframework.store import UserFrameworkStore

    _import(client)
    result = client.post("/f/ACME-1/delete", data={"confirm": "ACME-1"},
                         follow_redirects=False)
    assert result.status_code == 303
    assert UserFrameworkStore().list_frameworks() == []


def test_a_builtin_framework_cannot_be_deleted(client):
    """内置框架不是用户的东西，它随内容包走。"""
    result = client.get(f"/f/{BUILTIN}/delete")
    assert result.status_code == 400
    assert "Built-in" in result.text


def test_deleting_something_that_is_not_there_is_404(client):
    assert client.get("/f/NO-SUCH/delete").status_code == 404


def test_the_deletion_is_audited(client):
    """删掉几十小时的自评工作，日志里得有一行。"""
    from framework_reader.identity.store import IdentityStore

    _import(client)
    client.post("/f/ACME-1/delete", data={"confirm": "ACME-1"})
    events = [e["event"] for e in IdentityStore().audit(10)]
    assert "framework.delete" in events


def test_the_audit_line_says_what_went_with_it(client):
    from framework_reader.identity.store import IdentityStore

    _import(client)
    client.post("/f/ACME-1/delete", data={"confirm": "ACME-1"})
    entry = next(e for e in IdentityStore().audit(10)
                 if e["event"] == "framework.delete")
    assert "ACME-1" in entry["detail"]
    assert "1" in entry["detail"]
