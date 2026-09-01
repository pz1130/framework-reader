"""条款正文的编辑：所有条款都能改——导入条款改本行，内置条款贴覆盖层。

覆盖层哲学（与 all_interpretation 的逐字段覆盖同一套）：改的是用户库里的
这一份，内容库的官方基准一个字节不动，清空（删行）即恢复默认。
AI 帮改和字段重写同一道闸：提议稿回显在编辑框里，「保存」之前
一个字都不写库。original_text 那块墓碑不受影响（见
tests/test_no_original_text_write_path.py）——贴进来的原文进的是
用户自己的库，不出服务器。
"""
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier
from framework_reader.userframework.store import UserFrameworkStore, default_path

CID = "ACME-1:3.1"
BUILTIN_CID = "NIST-CSF-2.0:DE.CM-01"
CSV = "编号,标题,正文\n3.1,账号管理,应当为每人分配唯一账号。口令不得共享。\n"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(body_rewrite_runner=None):
        monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
        db = tmp_path / "content.sqlite"
        conn = sqlite3.connect(db)
        create_schema(conn)
        insert_frameworks(conn, [Framework(
            id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
            tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
        insert_controls(conn, [FrameworkControl(
            id=BUILTIN_CID, framework_id="NIST-CSF-2.0", label="Networks monitored",
            label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
        conn.close()

        from framework_reader.web.app import create_app

        return TestClient(
            create_app(db, body_rewrite_runner=body_rewrite_runner),
            follow_redirects=False)

    return _make


@pytest.fixture
def client(make_client):
    app = make_client()
    _import(app)
    return app


def _import(app):
    app.post("/import",
             data={"framework_id": "ACME-1", "name": "ACME 制度"},
             files={"file": ("f.csv", BytesIO(CSV.encode()), "text/csv")})


def _events(name: str) -> list[dict]:
    return [e for e in IdentityStore().audit(40) if e["event"] == name]


# ---------- 入口 ----------

def test_the_edit_link_shows_on_your_own_controls(client):
    page = client.get(f"/c/{CID}").text
    assert f'href="/c/{CID}/edit-body"' in page


def test_builtin_controls_offer_body_editing_too(client):
    """CSF 的正文由官方 label 兑现，直接展示；「改」在正文块里——
    改的是覆盖层，清空保存官方那版回来。"""
    page = client.get(f"/c/{BUILTIN_CID}").text
    assert "Networks monitored" in page
    assert "Official text" in page
    assert f'href="/c/{BUILTIN_CID}/edit-body"' in page


# ---------- 编辑页 ----------

def test_the_edit_page_prefills_the_body(client):
    page = client.get(f"/c/{CID}/edit-body").text
    assert "应当为每人分配唯一账号。口令不得共享。" in page
    assert "edit-body/ai" in page, "AI 帮改的入口要在同一页上"


def test_a_builtin_control_opens_an_empty_editor(client):
    result = client.get(f"/c/{BUILTIN_CID}/edit-body")
    assert result.status_code == 200
    assert "edit-body/ai" in result.text
    assert "应当为每人分配唯一账号" not in result.text


def test_missing_control_is_404(client):
    assert client.get("/c/NOPE:9.9/edit-body").status_code == 404


# ---------- 保存 ----------

def test_saving_writes_the_body_and_audits_it(client):
    result = client.post(f"/c/{CID}/edit-body",
                         data={"body": "应为每人分配唯一账号，口令九十天轮换。"})
    assert result.status_code == 303
    assert "九十天" in UserFrameworkStore().load_body(CID)
    detail = _events("control.body_edit")[0]["detail"]
    assert CID in detail and "chars ->" in detail, "只记大小不记正文"


def test_clearing_a_builtin_body_restores_the_default(client):
    """内置条款：存了再清空 = 恢复默认（删覆盖行），不是存一个空串。"""
    store = UserFrameworkStore()
    client.post(f"/c/{BUILTIN_CID}/edit-body", data={"body": "贴进来的 ISO 原文。"})
    assert store.load_body(BUILTIN_CID) == "贴进来的 ISO 原文。"
    assert client.post(f"/c/{BUILTIN_CID}/edit-body",
                       data={"body": ""}).status_code == 303
    assert store.load_body(BUILTIN_CID) is None


def test_a_pasted_body_shows_up_on_the_builtin_page(client):
    """贴进去的正文回条款页展示——QueryAPI.control_body 吃覆盖层。"""
    client.post(f"/c/{BUILTIN_CID}/edit-body",
                data={"body": "The organization shall define an ISMS policy."})
    page = client.get(f"/c/{BUILTIN_CID}").text
    assert "The organization shall define an ISMS policy." in page
    assert "paste in a passage" not in page, "有正文之后空态提示要退场"


def test_builtin_body_lands_in_override_not_user_control(client):
    client.post(f"/c/{BUILTIN_CID}/edit-body",
                data={"body": "The organization shall define a policy."})
    conn = sqlite3.connect(default_path())
    try:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM control_body_override "
            "WHERE control_id = ?", (BUILTIN_CID,)).fetchone()
        assert n == 1
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM user_control WHERE id = ?",
            (BUILTIN_CID,)).fetchone()
        assert n == 0, "内置 id 进 user_control 会撞 all_control 的 UNION ALL"
    finally:
        conn.close()


# ---------- AI 帮改：只出提议，不落库 ----------

def test_ai_proposal_shows_up_but_never_writes(make_client):
    def fake_runner(control_id, instruction, current):
        assert instruction == "语气改成制度体"
        return current + "（AI 改过的版本）"

    app = make_client(fake_runner)
    _import(app)
    page = app.post(f"/c/{CID}/edit-body/ai",
                    data={"body": "应当为每人分配唯一账号。",
                          "instruction": "语气改成制度体"}).text
    assert "（AI 改过的版本）" in page
    assert "（AI 改过的版本）" not in UserFrameworkStore().load_body(CID)


def test_ai_needs_an_instruction(client):
    result = client.post(f"/c/{CID}/edit-body/ai",
                         data={"body": "应当为每人分配唯一账号。",
                               "instruction": ""})
    assert result.status_code == 400
    assert "how should it change" in result.text


def test_the_prompt_keeps_the_model_hands_off_facts():
    from framework_reader.prompts import load_prompt

    prompt = load_prompt("body_rewrite")
    assert "Output only the rewritten body itself" in prompt
    assert "Do not invent system names" in prompt
