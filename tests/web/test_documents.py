"""在网页上传配套文档。见网页服务化设计 §8 S5

这些文字会被发给模型厂商，而且是本组织的内部制度——所以谁能传、谁能看、
传了什么、模型到底看到哪几段，四件事都要在界面上说得清。
"""
import io
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier
from framework_reader.userframework.documents import DocumentStore

POLICY = """第一章 日志管理

本公司各系统的安全日志留存不少于六个月，集中存放在日志平台，
每季度由安全组复核一次覆盖范围。
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    for role in ("author", "approver", "viewer"):
        identity.create_account(email=f"{role}@acme.cn", password="pw-role-role",
                                roles=(role,))
    return type("Env", (), {
        "app": create_app(db), "identity": identity, "docs": DocumentStore(),
    })()


def _as(env, role):
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": f"{role}@acme.cn",
                                "password": "pw-role-role"})
    return client


def _csrf(client):
    found = re.search(r'name="csrf" value="([^"]+)"', client.get("/frameworks").text)
    return found.group(1) if found else ""


def _upload(client, name="安全管理制度.txt", body=POLICY, title=""):
    return client.post("/documents", data={"csrf": _csrf(client), "title": title},
                       files={"file": (name, io.BytesIO(body.encode("utf-8")),
                                       "text/plain")})


# ---------- 谁能传、谁能看 ----------

def test_an_author_can_upload(env):
    assert _upload(_as(env, "author")).status_code == 303
    assert len(env.docs.list_documents()) == 1


def test_an_approver_can_read_but_not_upload(env):
    """签字前要能核对「这句话的依据是我们哪份文件的哪一段」。"""
    client = _as(env, "approver")
    assert client.get("/documents").status_code == 200
    assert _upload(client).status_code == 403


def test_a_viewer_cannot_see_them_at_all(env):
    """viewer 那一档是留给外部审计和刚入职的人的，而这里是内部制度全文。"""
    assert _as(env, "viewer").get("/documents").status_code == 403


def test_a_viewer_is_not_even_shown_the_link(env):
    assert 'href="/documents"' not in _as(env, "viewer").get("/frameworks").text


def test_an_admin_can_take_a_mis_upload_down(env):
    _upload(_as(env, "author"))
    doc = env.docs.list_documents()[0]
    boss = TestClient(env.app, follow_redirects=False)
    boss.post("/login", data={"email": "boss@acme.cn", "password": "pw-boss-boss"})
    boss.post(f"/documents/{doc.id}/delete", data={"csrf": _csrf(boss)})
    assert env.docs.list_documents() == []


# ---------- 传上去之后 ----------

def test_the_page_lists_what_was_uploaded(env):
    _upload(_as(env, "author"), title="安全管理制度")
    assert "安全管理制度" in _as(env, "author").get("/documents").text


def test_the_uploader_is_recorded(env):
    _upload(_as(env, "author"))
    assert env.docs.list_documents()[0].uploaded_by == "author@acme.cn"


def test_the_upload_lands_in_the_audit_log(env):
    _upload(_as(env, "author"))
    assert any(e["event"] == "document.upload" and e["actor"] == "author@acme.cn"
               for e in env.identity.audit())


def test_you_can_see_exactly_which_sections_the_model_will_get(env):
    """「模型到底看到了什么」不能只有我们知道。看不见就没人会信它。"""
    _upload(_as(env, "author"))
    doc = env.docs.list_documents()[0]
    page = _as(env, "author").get(f"/documents/{doc.id}").text
    assert "六个月" in page


def test_a_pdf_is_refused_with_a_sentence_not_a_stack_trace(env):
    response = _upload(_as(env, "author"), name="扫描件.pdf", body="%PDF-1.4")
    assert response.status_code == 400
    assert "Traceback" not in response.text
    assert "PDF" in response.text
    assert env.docs.list_documents() == []


def test_an_oversized_document_is_rejected_before_parsing(env, monkeypatch):
    from framework_reader.web import uploads

    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 8)
    response = _upload(_as(env, "author"), body="这是一份超过限制的制度")
    assert response.status_code == 413
    assert "over" in response.text
    assert env.docs.list_documents() == []


def test_the_page_warns_that_this_leaves_the_building(env):
    """上传内部制度 = 把它发给你配置的模型厂商。这句话必须写在传之前。"""
    page = _as(env, "author").get("/documents").text
    assert "model provider" in page


def test_the_page_says_not_to_upload_bought_standards(env):
    """ISO 原文放在我们的服务器上就是我们的问题。设计 §7"""
    assert "standard texts" in _as(env, "author").get("/documents").text


def test_the_api_schema_is_not_served_to_anyone(env):
    """Swagger 与 openapi.json 由 FastAPI 特殊注册，绕过守卫——等于一份
    不需要登录就能拿到的完整路由清单。"""
    client = TestClient(env.app, follow_redirects=False)
    for path in ("/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404
