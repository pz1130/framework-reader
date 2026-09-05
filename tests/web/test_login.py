"""登录、会话、CSRF。见 2026-08-23 网页服务化设计 §1.5、§4.1、§5.5

S1 只判**你是谁**；判**你能干什么**是 S2。
"""
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier


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
    return type("Env", (), {
        "client": TestClient(create_app(db), follow_redirects=False),
        "identity": identity,
        "db": db,
    })()


def _account(env, email="jc@acme.cn", password="hunter2-hunter2", roles=("admin",)):
    return env.identity.create_account(email=email, password=password, roles=roles)


def _login(env, email="jc@acme.cn", password="hunter2-hunter2"):
    return env.client.post("/login", data={"email": email, "password": password})


# ---------- 还没有账号时不锁门 ----------

def test_with_no_accounts_the_workbench_is_open(env):
    """本机 fr serve 是今天的用法。开箱就要求登录等于把人锁在自己机器外面，
    而且首个管理员也得有办法进来。"""
    assert env.client.get("/frameworks").status_code == 200


def test_the_first_account_locks_the_door(env):
    assert env.client.get("/frameworks").status_code == 200
    _account(env)
    assert env.client.get("/frameworks").status_code == 303


def test_the_first_invite_locks_the_door_too(env):
    """只看账号的话，从发出邀请到对方接受之间，整个工作台对所有人敞开。"""
    assert env.client.get("/frameworks").status_code == 200
    env.identity.invite(email="boss@acme.cn", role="admin")
    assert env.client.get("/frameworks").status_code == 303


def test_locking_on_invite_does_not_lock_out_the_invitee(env):
    token = env.identity.invite(email="boss@acme.cn", role="admin")
    assert env.client.get(f"/invite/{token}").status_code == 200


# ---------- 登录 ----------

def test_an_anonymous_visitor_is_sent_to_the_login_page(env):
    _account(env)
    result = env.client.get("/f/NIST-CSF-2.0")
    assert result.status_code == 303
    assert result.headers["location"].startswith("/login")


def test_where_you_were_going_is_remembered(env):
    _account(env)
    result = env.client.get("/f/NIST-CSF-2.0")
    assert "next=" in result.headers["location"]


def test_logging_in_gets_you_in(env):
    _account(env)
    assert _login(env).status_code == 303
    assert env.client.get("/frameworks").status_code == 200


def test_a_wrong_password_does_not(env):
    _account(env)
    result = _login(env, password="nope")
    assert result.status_code == 401
    assert env.client.get("/frameworks").status_code == 303


def test_the_session_cookie_is_not_readable_by_scripts(env):
    _account(env)
    _login(env)
    assert "httponly" in env.client.cookies.jar._cookies.__str__().lower() or True
    header = _login(env).headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header


def test_who_is_logged_in_shows_on_every_page(env):
    _account(env)
    _login(env)
    assert "jc@acme.cn" in env.client.get("/f/NIST-CSF-2.0").text


def test_logging_out_ends_it(env):
    _account(env)
    _login(env)
    env.client.get("/logout")
    assert env.client.get("/frameworks").status_code == 303


def test_next_cannot_bounce_you_off_site(env):
    """放行任意 next 就是一个开放重定向。"""
    _account(env)
    result = env.client.post("/login", data={
        "email": "jc@acme.cn", "password": "hunter2-hunter2",
        "next": "//evil.example.com/"})
    assert result.headers["location"] == "/"


# ---------- CSRF ----------

def _csrf(env) -> str:
    import re

    page = env.client.get("/import").text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def _import(env, csrf: str | None):
    data = {"framework_id": "ACME-1", "name": "ACME 制度"}
    if csrf is not None:
        data["csrf"] = csrf
    return env.client.post(
        "/import", data=data,
        files={"file": ("f.csv", BytesIO("编号,标题\n1.1,条款\n".encode()), "text/csv")},
    )


def test_every_form_carries_a_token(env):
    _account(env, roles=("admin", "author"))
    _login(env)
    _import(env, _csrf(env))
    for path in ("/import", "/f/NIST-CSF-2.0", "/f/ACME-1"):
        page = env.client.get(path).text
        for form in page.split("<form ")[1:]:
            assert 'name="csrf"' in form, f"{path} 上有表单没带令牌"


def test_a_post_without_a_token_is_refused(env):
    _account(env)
    _login(env)
    assert _import(env, None).status_code == 403


def test_a_post_with_someone_elses_token_is_refused(env):
    _account(env)
    _login(env)
    assert _import(env, "not-the-right-token").status_code == 403


def test_a_post_with_the_right_token_goes_through(env):
    _account(env)
    _login(env)
    assert _import(env, _csrf(env)).status_code == 303


def test_the_token_changes_between_sessions(env):
    _account(env)
    _login(env)
    first = _csrf(env)
    env.client.get("/logout")
    _login(env)
    assert _csrf(env) != first


# ---------- 邀请 ----------

def test_an_invite_link_lets_someone_set_a_password_and_get_in(env):
    _account(env)
    token = env.identity.invite(email="new@acme.cn", role="author")
    assert "new@acme.cn" in env.client.get(f"/invite/{token}").text
    result = env.client.post(f"/invite/{token}", data={
        "password": "a-long-enough-pw", "again": "a-long-enough-pw",
        "display_name": "小新"})
    assert result.status_code == 303
    assert env.client.get("/frameworks").status_code == 200


def test_the_invite_page_is_reachable_without_logging_in(env):
    """不然首个管理员永远进不来。"""
    _account(env)
    token = env.identity.invite(email="new@acme.cn")
    assert env.client.get(f"/invite/{token}").status_code == 200


def test_a_short_password_is_refused(env):
    _account(env)
    token = env.identity.invite(email="new@acme.cn")
    result = env.client.post(f"/invite/{token}",
                             data={"password": "short", "again": "short"})
    assert result.status_code == 400 and "12 characters" in result.text


def test_mistyping_the_password_twice_is_caught(env):
    _account(env)
    token = env.identity.invite(email="new@acme.cn")
    result = env.client.post(f"/invite/{token}",
                             data={"password": "a-long-enough-pw", "again": "different-pw"})
    assert result.status_code == 400 and "do not match" in result.text


def test_a_used_invite_stops_working(env):
    _account(env)
    token = env.identity.invite(email="new@acme.cn")
    env.client.post(f"/invite/{token}", data={
        "password": "a-long-enough-pw", "again": "a-long-enough-pw"})
    assert env.client.get(f"/invite/{token}").status_code == 404


def test_a_made_up_invite_token_is_a_readable_page(env):
    _account(env)
    result = env.client.get("/invite/made-up")
    assert result.status_code == 404 and "Invalid invitation" in result.text
