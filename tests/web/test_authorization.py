"""所有路由 × 所有角色，逐格对答案。见 2026-08-23 网页服务化设计 §1.5、§4.2

**这是这套东西唯一不会腐烂的办法。** 权限矩阵靠人记，三个月后必然和代码对不上。
新增路由忘了标注 → 这里红。

注意这不是拿表比表（那是同义反复），是**真发 HTTP 请求**再看结果——
守卫接错线、装饰器贴错位置、异常处理器吞掉 403，都只有这样才照得出来。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity import ROLES
from framework_reader.identity.permissions import allows
from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier
from framework_reader.web.auth import is_public

CID = "NIST-CSF-2.0:DE.CM-01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=CID, framework_id="NIST-CSF-2.0", label="Networks are monitored",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()

    from framework_reader.web.app import create_app

    identity = IdentityStore()
    # 先放一个 admin，否则「撤销最后一个 admin」的不变量会挡住建号
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    return type("Env", (), {
        "app": create_app(db), "identity": identity, "db": db,
    })()


def _client_as(env, role: str) -> TestClient:
    email = f"{role}@acme.cn"
    if env.identity.by_email(email) is None:
        env.identity.create_account(email=email, password="pw-role-role",
                                    roles=(role,))
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": email, "password": "pw-role-role"})
    return client


def _routes(env):
    for route in env.app.routes:
        if not hasattr(route, "methods") or route.path.startswith(
                ("/openapi", "/docs", "/redoc")):
            continue
        if is_public(route.path):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, route.endpoint


def _post(client, path: str, **data):
    """带上本次会话的 CSRF 令牌再 POST。

    不带令牌也会拿到 403——但那是 CSRF 拦的，不是授权拦的。用它断言授权，
    等于测了个假的：授权真出洞时这条测试照样绿。
    """
    page = client.get("/frameworks").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return client.post(path, data={"csrf": found.group(1) if found else "", **data})


def _concrete(path: str) -> str:
    return (path
            .replace("{framework_id}", "NIST-CSF-2.0")
            .replace("{control_id}", CID)
            .replace("{field}", "intent")
            .replace("{token}", "x"))


# ---------- 完整性 ----------

def test_every_route_declares_a_permission(env):
    """没标注的路由会被守卫拒绝。让它在这里坏，别让它在生产上坏。"""
    missing = [
        f"{m} {p}" for m, p, endpoint in _routes(env)
        if not getattr(endpoint, "__fr_permission__", None)
    ]
    assert not missing, f"这些路由没声明权限：{missing}"


def test_an_unlabelled_route_is_refused_not_allowed(env):
    """默认拒绝的真正含义：忘了标注 = 坏掉，不是放行。"""
    @env.app.get("/oops")
    def oops():
        return "不该看到我"

    client = _client_as(env, "admin")
    assert client.get("/oops").status_code == 403


# ---------- 逐格对答案 ----------

def test_the_matrix_holds_for_every_route_and_role(env):
    wrong = []
    for role in ROLES:
        client = _client_as(env, role)
        for method, path, endpoint in _routes(env):
            permission = endpoint.__fr_permission__
            expected_allowed = allows([role], permission)
            csrf = ""
            if method != "GET":
                page = client.get("/frameworks").text
                found = re.search(r'name="csrf" value="([^"]+)"', page)
                csrf = found.group(1) if found else ""
            response = client.request(
                method, _concrete(path),
                data={"csrf": csrf} if method != "GET" else None,
            )
            # 放行的路由可能因为缺参数返回 4xx，但**不会**是 403。
            actually_allowed = response.status_code != 403
            if actually_allowed != expected_allowed:
                wrong.append(
                    f"{role} {method} {path} 需要 {permission}："
                    f"应{'放行' if expected_allowed else '拒绝'}，"
                    f"实得 {response.status_code}")
    assert not wrong, "\n".join(wrong)


# ---------- 几条单独点名的 ----------

def test_a_viewer_cannot_confirm(env):
    client = _client_as(env, "viewer")
    assert _post(client, f"/c/{CID}/confirm").status_code == 403


def test_an_admin_cannot_confirm_either(env):
    """管理员能配置系统，不代表他懂这条控制。设计 §3。"""
    client = _client_as(env, "admin")
    assert _post(client, f"/c/{CID}/confirm").status_code == 403


def test_an_admin_cannot_spend_money_drafting(env):
    client = _client_as(env, "admin")
    assert _post(client, "/f/NIST-CSF-2.0/draft").status_code == 403


def test_an_approver_cannot_spend_money_drafting(env):
    client = _client_as(env, "approver")
    assert _post(client, "/f/NIST-CSF-2.0/draft").status_code == 403


def test_an_admin_who_grants_himself_approver_can_then_confirm(env):
    """提权是可以的，代价是留痕。"""
    account = env.identity.by_email("admin@acme.cn") or env.identity.create_account(
        email="admin@acme.cn", password="pw-role-role", roles=("admin",))
    client = _client_as(env, "admin")
    assert _post(client, f"/c/{CID}/confirm").status_code == 403
    env.identity.grant(account.id, "approver")
    assert _post(client, f"/c/{CID}/confirm").status_code != 403


def test_the_refusal_says_what_is_missing_and_what_you_have(env):
    client = _client_as(env, "viewer")
    body = _post(client, f"/c/{CID}/confirm").text
    assert "interpretation:confirm" in body and "viewer" in body


def test_a_viewer_can_still_read_and_export(env):
    client = _client_as(env, "viewer")
    assert client.get("/f/NIST-CSF-2.0").status_code == 200
    assert client.get("/f/NIST-CSF-2.0/soa.csv").status_code == 200


def test_nothing_is_enforced_when_login_is_not_configured(tmp_path, monkeypatch):
    """本机 fr serve 没有账号时照旧全放行——否则等于把人锁在自己机器外面。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "fresh"))
    db = tmp_path / "c.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    conn.close()

    from framework_reader.web.app import create_app

    client = TestClient(create_app(db), follow_redirects=False)
    assert client.get("/frameworks").status_code == 200


# ---------- 签字人是登录的那个人 ----------

def test_the_signature_records_the_logged_in_person(env):
    """用 getpass.getuser() 的话，全组织的签名都写成同一个名字——
    而「这段话有人认领」正是这个产品的立身之本。"""
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation,
    )
    from framework_reader.interpret.user_store import UserInterpretationStore
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    store = UserInterpretationStore()
    store.save(Interpretation(
        control_id="ACME-1:4.1",
        fields={n: Field(value="x", basis=Basis.INFERRED) for n in ALL_FIELDS}))

    client = _client_as(env, "approver")
    assert _post(client, "/c/ACME-1:4.1/confirm").status_code == 303
    assert store.load("ACME-1:4.1").provenance.confirmed_by == "approver@acme.cn"


def test_confirming_lands_in_the_audit_log(env):
    """确认是产品的核心动作，「谁认领的」必须可追溯。设计 §4.4"""
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation,
    )
    from framework_reader.interpret.user_store import UserInterpretationStore
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    UserInterpretationStore().save(Interpretation(
        control_id="ACME-1:4.1",
        fields={n: Field(value="x", basis=Basis.INFERRED) for n in ALL_FIELDS}))

    client = _client_as(env, "approver")
    _post(client, "/c/ACME-1:4.1/confirm")
    entries = env.identity.audit()
    assert any(e["event"] == "interpretation.confirm"
               and e["actor"] == "approver@acme.cn"
               and e["detail"] == "ACME-1:4.1" for e in entries)


# ---------- 按钮跟着权限走 ----------

def test_a_viewer_is_not_shown_buttons_he_cannot_use(env):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    page = _client_as(env, "viewer").get("/c/ACME-1:4.1").text
    assert "/edit/" not in page
    assert "I confirm this control" not in page
    assert "Draft this control" not in page


def test_an_author_is_shown_the_ones_he_can(env):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    page = _client_as(env, "author").get("/c/ACME-1:4.1").text
    assert "/edit/" in page and "Draft this control" in page
    assert "I confirm this control" not in page          # 起草的人不是签字的人


def test_a_viewer_sees_no_import_link(env):
    assert 'href="/import"' not in _client_as(env, "viewer").get("/frameworks").text


def test_a_viewer_is_not_shown_the_top_draft_button(env):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    page = _client_as(env, "viewer").get("/f/ACME-1").text
    top = page.split('class="pagein"', 1)[0]
    assert "/draft" not in top


def test_an_author_sees_the_top_draft_button_with_csrf(env):
    """顶栏的一键起草也是 POST，漏令牌点了就是 403。"""
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    page = _client_as(env, "author").get("/f/ACME-1").text
    top = page.split('class="pagein"', 1)[0]
    assert 'action="/f/ACME-1/draft"' in top
    assert 'name="csrf"' in top
    assert "Draft these 1 controls" in top
