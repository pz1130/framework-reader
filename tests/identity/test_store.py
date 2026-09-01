"""身份层。见 2026-08-23 网页服务化设计 §2、§4

S1 只做**身份**（你是谁）。授权（你能干什么）是 S2。
"""
from datetime import timedelta

import pytest

from framework_reader.identity import DEFAULT_ROLE
from framework_reader.identity.store import IdentityError, IdentityStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return IdentityStore()


def _admin(store, email="boss@acme.cn"):
    return store.create_account(email=email, password="pw-boss", roles=("admin",))


# ---------- 账号 ----------

def test_a_new_account_can_be_found_by_email(store):
    _admin(store)
    assert store.by_email("boss@acme.cn").email == "boss@acme.cn"


def test_email_is_matched_case_insensitively(store):
    store.create_account(email="Boss@ACME.cn", password="pw")
    assert store.by_email("boss@acme.cn") is not None


def test_the_default_role_is_read_only(store):
    """默认值决定了忘记配置时会发生什么，而忘记配置是常态。"""
    account = store.create_account(email="new@acme.cn", password="pw")
    assert account.roles == {DEFAULT_ROLE} == {"viewer"}


def test_two_accounts_cannot_share_an_email(store):
    _admin(store)
    with pytest.raises(IdentityError, match="already has an account"):
        store.create_account(email="boss@acme.cn", password="x")


def test_a_bad_email_is_refused(store):
    with pytest.raises(IdentityError, match="email address looks wrong"):
        store.create_account(email="not-an-email", password="x")


def test_an_unknown_role_is_refused(store):
    with pytest.raises(IdentityError, match="no such role"):
        store.create_account(email="x@acme.cn", password="x", roles=("god",))


# ---------- 角色是加法的 ----------

def test_roles_add_up(store):
    """一个人可以同时是 author 和 approver，权限取并集（设计 §1.1）。"""
    account = store.create_account(email="both@acme.cn", password="pw",
                                   roles=("author", "approver"))
    assert store.by_email("both@acme.cn").roles == {"author", "approver"}
    assert account.roles == {"author", "approver"}


def test_granting_twice_is_harmless(store):
    account = _admin(store)
    store.grant(account.id, "author")
    store.grant(account.id, "author")
    assert "author" in store.by_id(account.id).roles


def test_the_last_admin_cannot_be_demoted(store):
    """撤了就没人能管系统，只能改库救（设计 §4.3）。"""
    account = _admin(store)
    with pytest.raises(IdentityError, match="last admin"):
        store.revoke(account.id, "admin")


def test_the_last_admin_cannot_be_disabled(store):
    account = _admin(store)
    with pytest.raises(IdentityError, match="last admin"):
        store.set_status(account.id, "disabled")


def test_a_second_admin_makes_the_first_demotable(store):
    first = _admin(store)
    _admin(store, "boss2@acme.cn")
    store.revoke(first.id, "admin")
    assert "admin" not in store.by_id(first.id).roles


def test_disabling_someone_kills_their_session_now(store):
    """留着会话等于停用没生效。"""
    _admin(store)
    victim = store.create_account(email="gone@acme.cn", password="pw")
    session = store.login("gone@acme.cn", "pw")
    store.set_status(victim.id, "disabled")
    assert store.resume(session.token) is None


# ---------- 登录 ----------

def test_the_right_password_gets_a_session(store):
    _admin(store)
    session = store.login("boss@acme.cn", "pw-boss")
    assert session.account.email == "boss@acme.cn" and session.token


def test_the_wrong_password_does_not(store):
    _admin(store)
    with pytest.raises(IdentityError):
        store.login("boss@acme.cn", "nope")


def test_an_unknown_email_says_exactly_what_a_wrong_password_says(store):
    """区别开就是一个账号枚举接口。"""
    _admin(store)
    with pytest.raises(IdentityError) as unknown:
        store.login("nobody@acme.cn", "pw-boss")
    with pytest.raises(IdentityError) as wrong:
        store.login("boss@acme.cn", "nope")
    assert str(unknown.value) == str(wrong.value)


def test_a_disabled_account_cannot_log_in(store):
    _admin(store)
    victim = store.create_account(email="gone@acme.cn", password="pw")
    store.set_status(victim.id, "disabled")
    with pytest.raises(IdentityError):
        store.login("gone@acme.cn", "pw")


def test_an_sso_only_account_cannot_log_in_with_a_blank_password(store):
    """password_hash 为空 = 只能走 SSO。空口令不该当成「口令对上了」。"""
    store.create_account(email="sso@acme.cn")
    with pytest.raises(IdentityError):
        store.login("sso@acme.cn", "")


# ---------- 会话 ----------

def test_a_session_resumes_from_its_token(store):
    _admin(store)
    session = store.login("boss@acme.cn", "pw-boss")
    assert store.resume(session.token).account.email == "boss@acme.cn"


def test_the_raw_token_is_never_stored(store):
    """库泄漏不该等于所有会话被接管。"""
    import sqlite3

    _admin(store)
    session = store.login("boss@acme.cn", "pw-boss")
    conn = sqlite3.connect(store.path)
    ids = [r[0] for r in conn.execute("SELECT id FROM session")]
    conn.close()
    assert session.token not in ids


def test_logging_out_kills_the_session(store):
    _admin(store)
    session = store.login("boss@acme.cn", "pw-boss")
    store.logout(session.token)
    assert store.resume(session.token) is None


def test_an_expired_session_is_gone(store, monkeypatch):
    """绝对过期写在建会话那一刻，所以补丁要打在登录之前——
    事后改常量不会回溯已经发出去的会话，这本身就是我们要的语义。"""
    import framework_reader.identity.store as module

    _admin(store)
    monkeypatch.setattr(module, "ABSOLUTE_TTL", timedelta(seconds=-1))
    session = store.login("boss@acme.cn", "pw-boss")
    assert store.resume(session.token) is None


def test_an_idle_session_is_gone(store, monkeypatch):
    import framework_reader.identity.store as module

    _admin(store)
    session = store.login("boss@acme.cn", "pw-boss")
    monkeypatch.setattr(module, "IDLE_TTL", timedelta(seconds=-1))
    assert store.resume(session.token) is None


def test_a_garbage_token_is_just_no_session(store):
    assert store.resume("not-a-token") is None
    assert store.resume("") is None


def test_every_session_gets_its_own_csrf_token(store):
    _admin(store)
    a = store.login("boss@acme.cn", "pw-boss")
    b = store.login("boss@acme.cn", "pw-boss")
    assert a.csrf != b.csrf


# ---------- 邀请 ----------

def test_an_invite_can_be_accepted_once(store):
    token = store.invite(email="new@acme.cn", role="author")
    account = store.accept_invite(token, password="pw-new")
    assert account.email == "new@acme.cn" and account.roles == {"author"}


def test_the_same_invite_cannot_be_used_twice(store):
    token = store.invite(email="new@acme.cn")
    store.accept_invite(token, password="pw-new")
    with pytest.raises(IdentityError, match="invalid or expired"):
        store.accept_invite(token, password="pw-again")


def test_an_expired_invite_is_refused(store, monkeypatch):
    import framework_reader.identity.store as module

    token = store.invite(email="new@acme.cn")
    monkeypatch.setattr(module, "INVITE_TTL", timedelta(seconds=-1))
    fresh = store.invite(email="other@acme.cn")
    assert store.peek_invite(fresh) is None
    assert store.peek_invite(token) is not None      # 早先发的那张不受影响


def test_the_raw_invite_token_is_never_stored(store):
    import sqlite3

    token = store.invite(email="new@acme.cn")
    conn = sqlite3.connect(store.path)
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM invite")]
    conn.close()
    assert token not in stored


def test_inviting_someone_who_already_has_an_account_is_refused(store):
    _admin(store)
    with pytest.raises(IdentityError, match="already has an account"):
        store.invite(email="boss@acme.cn")


def test_a_bogus_invite_token_shows_nothing(store):
    assert store.peek_invite("made-up") is None


# ---------- 审计 ----------

def test_a_successful_login_is_logged(store):
    _admin(store)
    store.login("boss@acme.cn", "pw-boss")
    assert any(e["event"] == "login.ok" for e in store.audit())


def test_a_failed_login_is_logged(store):
    _admin(store)
    with pytest.raises(IdentityError):
        store.login("boss@acme.cn", "nope")
    assert any(e["event"] == "login.failed" for e in store.audit())


def test_the_audit_log_does_not_record_the_password(store):
    _admin(store)
    with pytest.raises(IdentityError):
        store.login("boss@acme.cn", "hunter2")
    assert not any("hunter2" in str(e) for e in store.audit())


# ---------- 不能给自己提权（设计 §4.3） ----------

def test_an_admin_cannot_grant_himself_a_role(store):
    """提权要另一个人点头。默认开着。"""
    boss = _admin(store)
    with pytest.raises(IdentityError):
        store.grant(boss.id, "approver", by=boss.id)
    assert "approver" not in store.by_email(boss.email).roles


def test_granting_someone_else_is_fine(store):
    boss = _admin(store)
    other = store.create_account(email="a@acme.cn")
    store.grant(other.id, "author", by=boss.id)
    assert "author" in store.by_email("a@acme.cn").roles


def test_the_cli_is_not_anybody_so_it_is_not_self_grant(store):
    """单管理员的组织从 CLI 救场——那条路不该被这条不变量挡住。"""
    boss = _admin(store)
    store.grant(boss.id, "author", by="cli")
    assert "author" in store.by_email(boss.email).roles


def test_the_switch_can_be_turned_off(store):
    """单 admin 的小组织下这条会挡路，所以做成可关闭的开关。"""
    boss = _admin(store)
    store.set_self_grant(True, by=boss.email)
    store.grant(boss.id, "approver", by=boss.id)
    assert "approver" in store.by_email(boss.email).roles


def test_turning_the_switch_off_leaves_a_trace(store):
    boss = _admin(store)
    store.set_self_grant(True, by=boss.email)
    assert any(e["event"] == "setting.self_grant" and e["actor"] == boss.email
               for e in store.audit())


def test_the_switch_is_on_by_default(store):
    _admin(store)
    assert store.self_grant_allowed() is False


def test_giving_up_your_own_role_is_not_self_grant(store):
    """撤自己的角色是降权，不是提权——只要不是最后一个 admin。"""
    _admin(store)
    boss2 = store.create_account(email="b@acme.cn", roles=("admin", "author"))
    store.revoke(boss2.id, "author", by=boss2.id)
    assert "author" not in store.by_email("b@acme.cn").roles


# ---------- Entra 落到同一张 membership 表（设计 §5.3、§5.4） ----------

def _claims(**over):
    from framework_reader.identity.entra import EntraClaims

    base = {"oid": "oid-ann", "email": "ann@acme.cn", "display_name": "安然",
            "roles": frozenset({"author"})}
    base.update(over)
    return EntraClaims(**base)


def test_the_first_sso_login_creates_the_account(store):
    session = store.sign_in_entra(_claims())
    assert session.account.email == "ann@acme.cn"
    assert session.account.roles == frozenset({"author"})


def test_the_same_person_is_recognised_by_oid_not_by_email(store):
    """改名、换域、别名——email 会变。拿它当主键，用户改个名就成了新人。"""
    first = store.sign_in_entra(_claims())
    second = store.sign_in_entra(_claims(email="an.ran@acme.cn"))
    assert second.account.id == first.account.id
    assert second.account.email == "an.ran@acme.cn"


def test_two_different_oids_are_two_different_people(store):
    a = store.sign_in_entra(_claims())
    b = store.sign_in_entra(_claims(oid="oid-bob", email="bob@acme.cn"))
    assert a.account.id != b.account.id


def test_an_invited_local_account_is_linked_not_duplicated(store):
    """先发邮箱邀请、后接 Entra 是最常见的路径。不认这条会长出两个同名账号。"""
    local = store.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                                 roles=("approver",))
    session = store.sign_in_entra(_claims())
    assert session.account.id == local.id
    assert len(store.list_accounts()) == 1


def test_the_sync_is_one_way_so_hand_edits_are_overwritten(store):
    """写在界面上：Entra 是权威，我们这边的手工调整下次登录就没了。"""
    session = store.sign_in_entra(_claims())
    store.grant(session.account.id, "approver", by="cli")
    again = store.sign_in_entra(_claims())
    assert again.account.roles == frozenset({"author"})


def test_the_sync_will_not_remove_the_last_admin(store):
    """Entra 那边少配一个 App Role，就没人能管系统了——那不能是一次登录的副作用。"""
    session = store.sign_in_entra(_claims(roles=frozenset({"admin"})))
    again = store.sign_in_entra(_claims(roles=frozenset({"viewer"})))
    assert "admin" in again.account.roles


def test_the_sync_never_touches_a_local_only_account(store):
    local = store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                                 roles=("admin",))
    store.sign_in_entra(_claims())
    assert store.by_id(local.id).roles == frozenset({"admin"})


def test_a_disabled_account_cannot_come_in_through_sso_either(store):
    session = store.sign_in_entra(_claims())
    _admin(store)                                   # 先留一个 admin，否则停用会被挡
    store.set_status(session.account.id, "disabled")
    with pytest.raises(IdentityError):
        store.sign_in_entra(_claims())


def test_an_sso_login_is_audited(store):
    store.sign_in_entra(_claims())
    assert any(e["event"] == "login.ok" and e["actor"] == "ann@acme.cn"
               for e in store.audit())


def test_a_role_change_from_entra_is_audited(store):
    """授权变更就是授权变更，来自 Entra 也一样要留痕。"""
    store.sign_in_entra(_claims())
    store.sign_in_entra(_claims(roles=frozenset({"approver"})))
    assert any(e["event"] == "role.sync" for e in store.audit())


# ---------- 登录途中的一次性状态 ----------

def test_a_flow_can_be_taken_exactly_once(store):
    state, nonce, verifier = store.start_oidc_flow("/f/ACME-1")
    taken = store.take_oidc_flow(state)
    assert taken["nonce"] == nonce and taken["verifier"] == verifier
    assert taken["next_url"] == "/f/ACME-1"
    assert store.take_oidc_flow(state) is None


def test_a_made_up_state_gets_nothing(store):
    assert store.take_oidc_flow("never-issued") is None


def test_an_old_flow_expires(store, monkeypatch):
    from framework_reader.identity import store as module

    state, _, _ = store.start_oidc_flow("/")
    later = module._now() + timedelta(hours=1)
    monkeypatch.setattr(module, "_now", lambda: later)
    assert store.take_oidc_flow(state) is None


def test_every_flow_gets_its_own_nonce_and_verifier(store):
    a = store.start_oidc_flow("/")
    b = store.start_oidc_flow("/")
    assert a[1] != b[1] and a[2] != b[2]
