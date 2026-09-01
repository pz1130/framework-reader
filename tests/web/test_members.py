"""成员管理的网页界面。见 2026-08-23 网页服务化设计 §4.3

管账号原先只有 CLI（`fr account grant`）。托管服务里管理员未必有服务器的
shell——**能在 CLI 做而界面上做不了的管理动作，等于没做**。

这一页也是「不能给自己加角色」那条不变量的唯一出口：开关在这里关，
关掉进审计日志。
"""
import re
import sqlite3

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
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                            roles=("author",))
    return type("Env", (), {"app": create_app(db), "identity": identity})()


def _as(env, email: str, password: str) -> TestClient:
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": email, "password": password})
    return client


def _boss(env) -> TestClient:
    return _as(env, "boss@acme.cn", "pw-boss-boss")


def _ann(env) -> TestClient:
    return _as(env, "ann@acme.cn", "pw-ann-ann-ann")


def _post(client, path: str, **data):
    page = client.get("/members").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return client.post(path, data={"csrf": found.group(1) if found else "", **data})


def _id(env, email: str) -> str:
    return env.identity.by_email(email).id


# ---------- 看得见 ----------

def test_the_members_page_lists_everyone_and_their_roles(env):
    page = _boss(env).get("/members").text
    assert "boss@acme.cn" in page and "ann@acme.cn" in page
    assert "author" in page


def test_every_page_has_a_way_to_get_here(env):
    """藏在 URL 里的管理页等于没有。

    2026-08-24 起成员页收进了「设置」，所以是两跳：首页 → 设置 → 成员。
    断言仍然逐跳走，不直接打 /members——要保的是「点得到」，不是「有这个地址」。
    """
    client = _boss(env)
    assert 'href="/settings"' in client.get("/frameworks").text
    assert 'href="/members"' in client.get("/settings").text


def test_an_author_can_see_who_is_who_but_not_change_it(env):
    """member:read 给了所有人——协作里「找谁签字」是每天都要问的。"""
    page = _ann(env).get("/members").text
    assert "boss@acme.cn" in page
    assert "Send invitation" not in page


def test_an_author_cannot_grant_by_hand(env):
    """按钮藏起来是体验，拒绝是授权。两件事分开验。"""
    target = _id(env, "ann@acme.cn")
    assert _post(_ann(env), f"/members/{target}/role",
                 grant="admin").status_code == 403


# ---------- 邀请 ----------

def test_an_admin_can_invite_from_the_page(env):
    page = _post(_boss(env), "/members/invite",
                 email="new@acme.cn", role="approver").text
    assert "/invite/" in page
    assert env.identity.peek_invite(
        re.search(r"/invite/([A-Za-z0-9_-]+)", page).group(1))["role"] == "approver"


def test_the_invite_link_is_shown_once_and_not_stored_in_the_clear(env):
    client = _boss(env)
    page = _post(client, "/members/invite", email="new@acme.cn", role="viewer").text
    token = re.search(r"/invite/([A-Za-z0-9_-]+)", page).group(1)
    assert token not in client.get("/members").text


def test_inviting_an_existing_email_says_so_instead_of_crashing(env):
    page = _post(_boss(env), "/members/invite",
                 email="ann@acme.cn", role="viewer").text
    assert "already has an account" in page


# ---------- 角色 ----------

def test_an_admin_can_grant_a_role(env):
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/role", grant="approver")
    assert "approver" in env.identity.by_email("ann@acme.cn").roles


def test_an_admin_can_revoke_a_role(env):
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/role", revoke="author")
    assert "author" not in env.identity.by_email("ann@acme.cn").roles


def test_a_role_change_lands_in_the_audit_log(env):
    """提权是所有事故的第一步。设计 §4.4"""
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/role", grant="approver")
    assert any(e["event"] == "role.grant" and e["actor"] == "boss@acme.cn"
               and "ann@acme.cn" in e["detail"] for e in env.identity.audit())


def test_the_last_admin_cannot_be_demoted_from_the_page_either(env):
    """不变量在存储层，但界面上要说人话，不能是 500。"""
    response = _post(_boss(env), f"/members/{_id(env, 'boss@acme.cn')}/role",
                     revoke="admin")
    assert response.status_code != 500
    assert "This is the last admin" in response.text
    assert "admin" in env.identity.by_email("boss@acme.cn").roles


# ---------- 不能给自己加角色 ----------

def test_an_admin_cannot_grant_himself_approver_from_the_page(env):
    response = _post(_boss(env), f"/members/{_id(env, 'boss@acme.cn')}/role",
                     grant="approver")
    assert "approver" not in env.identity.by_email("boss@acme.cn").roles
    assert "cannot grant roles to yourself" in response.text


def test_the_page_says_the_switch_is_on(env):
    assert "cannot grant themselves roles" in _boss(env).get("/members").text


def test_turning_the_switch_off_lets_him_through(env):
    client = _boss(env)
    _post(client, "/members/self-grant", allowed="1")
    _post(client, f"/members/{_id(env, 'boss@acme.cn')}/role", grant="approver")
    assert "approver" in env.identity.by_email("boss@acme.cn").roles


def test_turning_the_switch_off_is_audited(env):
    _post(_boss(env), "/members/self-grant", allowed="1")
    assert any(e["event"] == "setting.self_grant" and e["actor"] == "boss@acme.cn"
               for e in env.identity.audit())


def test_an_author_cannot_touch_the_switch(env):
    assert _post(_ann(env), "/members/self-grant", allowed="1").status_code == 403


# ---------- 停用 ----------

def test_an_admin_can_disable_someone(env):
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/status", status="disabled")
    assert env.identity.by_email("ann@acme.cn").active is False


def test_disabling_cuts_the_session_immediately(env):
    """停用了还能接着用，等于停用没生效。"""
    ann = _ann(env)
    assert ann.get("/frameworks").status_code == 200
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/status", status="disabled")
    assert ann.get("/frameworks").status_code == 303


def test_the_last_admin_cannot_disable_himself(env):
    response = _post(_boss(env), f"/members/{_id(env, 'boss@acme.cn')}/status",
                     status="disabled")
    assert "This is the last admin" in response.text
    assert env.identity.by_email("boss@acme.cn").active is True


def test_an_unknown_account_is_a_readable_404(env):
    response = _post(_boss(env), "/members/nobody/role", grant="viewer")
    assert response.status_code == 404


# ---------- 审计日志看得见 ----------

def test_an_admin_can_read_the_audit_log_in_the_browser(env):
    _post(_boss(env), f"/members/{_id(env, 'ann@acme.cn')}/role", grant="approver")
    page = _boss(env).get("/audit").text
    assert "role.grant" in page and "ann@acme.cn" in page


def test_an_author_cannot_read_the_audit_log(env):
    assert _ann(env).get("/audit").status_code == 403


def test_the_invite_role_defaults_to_read_only(env):
    """下拉框的默认值和「新账号默认 viewer」是同一条规矩：
    默认值决定了点快了会发生什么，而点快了是常态。"""
    page = _boss(env).get("/members").text
    assert '<option value="viewer" selected>' in page
    assert '<option value="admin" selected>' not in page


# ---------- 第一个管理员 ----------
#
# 在这之前，首个账号只能靠终端里的 `fr account invite`，而成员页的入口在
# 「还没有账号」时是**藏起来的**。于是本机跑 `fr serve` 的人在界面上找不到
# 用户管理——不是因为没做，是因为入口被单人模式的判断顺手挡掉了。
# 能在 CLI 做而界面上做不了的管理动作，等于没做。


@pytest.fixture
def solo(tmp_path, monkeypatch):
    """一个账号都没有：本机单人，门没锁。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db), follow_redirects=False)


def test_the_members_page_asks_for_the_first_admin_when_there_is_nobody(solo):
    page = solo.get("/members").text
    assert 'action="/members/bootstrap"' in page
    # 名单和邀请块这时都是空的，别摆出来
    assert 'action="/members/invite"' not in page


def test_creating_the_first_admin_logs_you_in_as_one(solo):
    response = solo.post("/members/bootstrap", data={
        "email": "boss@acme.cn", "display_name": "老板",
        "password": "pw-boss-boss", "again": "pw-boss-boss"})
    assert response.status_code == 303
    account = IdentityStore().by_email("boss@acme.cn")
    assert account is not None and account.roles == frozenset({"admin"})
    # 直接种会话：门在这一刻锁上，让人刚建完就被踢出去很蠢
    assert solo.get("/members").status_code == 200


def test_creating_the_first_admin_locks_the_door_for_everyone_else(solo):
    solo.post("/members/bootstrap", data={
        "email": "boss@acme.cn", "password": "pw-boss-boss",
        "again": "pw-boss-boss"})
    stranger = TestClient(solo.app, follow_redirects=False)
    response = stranger.get("/frameworks")
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_the_first_admin_shows_up_in_the_audit_log(solo):
    solo.post("/members/bootstrap", data={
        "email": "boss@acme.cn", "password": "pw-boss-boss",
        "again": "pw-boss-boss"})
    assert "account.bootstrap" in solo.get("/audit").text


def test_this_door_is_dead_once_anybody_has_an_account(env):
    """否则它就是一条绕过邀请、给自己发管理员的路。

    拒得是 409 不是 403：这套代码里 403 专指「你这个角色不能做这件事」，
    而授权矩阵的遍历测试就靠这条约定分辨真假。这里拒的理由和角色无关——
    门关了，谁来都一样。
    """
    response = _post(_boss(env), "/members/bootstrap",
                     email="mole@acme.cn", password="pw-mole-mole",
                     again="pw-mole-mole")
    assert response.status_code == 409
    assert env.identity.by_email("mole@acme.cn") is None


@pytest.mark.parametrize("data, says", [
    ({"email": "boss@acme.cn", "password": "short", "again": "short"}, "12 characters"),
    ({"email": "boss@acme.cn", "password": "pw-boss-boss", "again": "pw-boss-bos"},
     "do not match"),
    ({"email": "boss", "password": "pw-boss-boss", "again": "pw-boss-boss"},
     "email address looks wrong"),
])
def test_a_bad_first_admin_is_refused_and_nothing_is_created(solo, data, says):
    response = solo.post("/members/bootstrap", data=data)
    assert response.status_code == 400
    assert says in response.text
    assert IdentityStore().list_accounts() == []


# ---------- 角色在界面上是中文 ----------

def test_the_roles_read_in_english(env):
    page = _boss(env).get("/members").text
    for name in ("Admin", "Editor", "Approver", "Viewer"):
        assert name in page, name


def test_the_english_role_ids_stay_reachable_for_the_cli(env):
    """`fr account grant x author` 用的是英文名，界面纯中文会让人对不上号。"""
    page = _boss(env).get("/members").text
    assert "author" in page and "viewer" in page
