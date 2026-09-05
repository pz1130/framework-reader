"""审阅队列：一次一条初稿，确认或跳过，键盘翻页。

签字必须一条一条签——队列省掉的只是「找下一条看什么」，不是「逐条过眼」。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.interpret.user_store import UserInterpretationStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

FW = "NIST-CSF-2.0"
A, B, C = (f"{FW}:DE.CM-01", f"{FW}:DE.CM-02", f"{FW}:PR.AA-01")


def _draft(control_id: str) -> Interpretation:
    return Interpretation(
        control_id=control_id, state=InterpretationState.DRAFT,
        fields={
            name: Field(value=f"起草的{name}", basis=Basis.INFERRED)
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance())


@pytest.fixture
def env(tmp_path, monkeypatch):
    """两份草稿在用户库（网页起草的 overlay 形态）；C 没有解读，不进队列。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=FW, name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [
        FrameworkControl(
            id=cid, framework_id=FW, label=f"控制 {cid.split(':')[-1]}",
            label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)
        for cid in (A, B, C)
    ])
    conn.close()

    store = UserInterpretationStore()
    store.save(_draft(A))
    store.save(_draft(B))

    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="ok@acme.cn", password="pw-ok-ok-ok",
                            roles=("approver",))
    return type("Env", (), {"app": create_app(db), "identity": identity})()


def _client_as(env, email: str, password: str) -> TestClient:
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": email, "password": password})
    return client


def _csrf(client) -> str:
    page = client.get("/frameworks").text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def test_the_queue_serves_the_first_unconfirmed_draft(env):
    page = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok").get("/review").text
    assert f'href="/c/{A}"' in page
    assert f'href="/c/{B}"' not in page          # 一次只上这一条
    assert "起草的intent" in page
    assert "<strong>1</strong> / 2 left to confirm" in page


def test_confirm_from_the_queue_lands_on_the_next_one(env):
    client = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok")
    result = client.post(
        f"/c/{A}/confirm", data={"csrf": _csrf(client), "next": "1"},
        follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/review"
    page = client.get("/review").text
    assert f'href="/c/{B}"' in page
    assert f'href="/c/{A}"' not in page
    assert "<strong>0</strong> / 1 left to confirm" in page


def test_the_queue_empties_out(env):
    client = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok")
    for cid in (A, B):
        client.post(f"/c/{cid}/confirm", data={"csrf": _csrf(client)})
    assert "The queue is empty" in client.get("/review").text


def test_skip_moves_on_without_signing(env):
    """跳过不是签字。B 只是这一眼先看，A 原样留在队里。"""
    client = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok")
    page = client.get("/review", params={"after": A}).text
    assert f'href="/c/{B}"' in page
    assert UserInterpretationStore().load(A).state is InterpretationState.DRAFT


def test_a_viewer_sees_the_queue_but_no_confirm_button(env):
    """看是 content:read，签是 interpretation:confirm——页面上只该少按钮。"""
    page = _client_as(env, "boss@acme.cn", "pw-boss-boss").get("/review").text
    assert f'href="/c/{A}"' in page
    assert "Confirm and next" not in page
    assert "Skip" in page


def test_the_home_desk_counts_the_backlog(env):
    client = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok")
    home = client.get("/").text
    assert 'href="/review"' in home
    assert "AI drafts awaiting confirmation" in home


def test_the_home_desk_is_quiet_when_all_signed(env):
    client = _client_as(env, "ok@acme.cn", "pw-ok-ok-ok")
    for cid in (A, B):
        client.post(f"/c/{cid}/confirm", data={"csrf": _csrf(client)})
    assert "AI drafts awaiting confirmation" not in client.get("/").text
