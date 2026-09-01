"""用公司账号登录。见 2026-08-23 网页服务化设计 §5

假 IdP 在 `tests/identity/test_entra.py` 里已经验过令牌那一层；这里验的是
**接线**：state 一次性、cookie 换新、角色落到同一张 membership 表、
配了 Entra 就锁门。
"""
import json
import re
import sqlite3
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from framework_reader.identity.entra import EntraConfig
from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "app-client-id"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeEntra:
    def __init__(self):
        self.roles = ["author"]
        self.oid = "oid-ann"
        self.email = "ann@acme.cn"
        self.nonce_seen = ""
        self.code_seen = ""

    def fetch(self, method, url, data=None):
        if url.endswith("openid-configuration"):
            return {"issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/keys"}
        if url.endswith("/keys"):
            raw = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
            raw.update({"kid": "kid-1", "use": "sig", "alg": "RS256"})
            return {"keys": [raw]}
        if url.endswith("/token"):
            self.code_seen = data["code"]
            return {"id_token": self._token()}
        raise AssertionError(url)

    def _token(self):
        return jwt.encode({
            "iss": ISSUER, "aud": CLIENT_ID, "tid": TENANT, "oid": self.oid,
            "preferred_username": self.email, "name": "安然",
            "roles": self.roles, "nonce": self.nonce_seen,
            "iat": int(time.time()), "nbf": int(time.time()),
            "exp": int(time.time()) + 600,
        }, KEY, algorithm="RS256", headers={"kid": "kid-1"})


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

    idp = FakeEntra()
    app = create_app(
        db,
        entra=EntraConfig(tenant_id=TENANT, client_id=CLIENT_ID,
                          client_secret="shh",
                          redirect_uri="https://fr.acme.cn/auth/entra/callback"),
        entra_fetch=idp.fetch,
    )
    return type("Env", (), {
        # https：接了 Entra 的部署就是 https，而 Secure cookie 在 http 上不发。
        "client": TestClient(app, base_url="https://fr.acme.cn",
                             follow_redirects=False),
        "identity": IdentityStore(), "idp": idp, "db": db,
    })()


def _walk_in(env, next_url: str = "/"):
    """走一遍完整的往返：点按钮 → IdP → 回调。"""
    start = env.client.get(f"/auth/entra?next={next_url}")
    location = start.headers["location"]
    state = re.search(r"state=([^&]+)", location).group(1)
    env.idp.nonce_seen = re.search(r"nonce=([^&]+)", location).group(1)
    return env.client.get(f"/auth/entra/callback?code=the-code&state={state}")


# ---------- 门 ----------

def test_configuring_entra_locks_the_door_even_with_no_accounts(env):
    """接了 IdP 就说明这是联网部署。这时还敞着，等于第一个人是任何人。"""
    assert env.client.get("/frameworks").status_code == 303


def test_the_login_page_offers_the_company_account(env):
    assert "Sign in with your company account" in env.client.get("/login").text


def test_without_entra_the_login_page_does_not_offer_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "h2"))
    db = tmp_path / "c.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    conn.close()
    from framework_reader.web.app import create_app

    store = IdentityStore()
    store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                         roles=("admin",))
    client = TestClient(create_app(db), follow_redirects=False)
    assert "Sign in with your company account" not in client.get("/login").text


# ---------- 往返 ----------

def test_a_full_round_trip_gets_you_in(env):
    response = _walk_in(env)
    assert response.status_code == 303
    assert env.client.get("/frameworks").status_code == 200


def test_the_account_lands_in_the_same_membership_table(env):
    _walk_in(env)
    account = env.identity.by_email("ann@acme.cn")
    assert account.roles == frozenset({"author"})


def test_the_app_role_decides_the_role(env):
    env.idp.roles = ["approver"]
    _walk_in(env)
    assert env.identity.by_email("ann@acme.cn").roles == frozenset({"approver"})


def test_where_you_were_going_is_remembered(env):
    assert _walk_in(env, "/f/NIST-CSF-2.0").headers["location"] == "/f/NIST-CSF-2.0"


def test_next_cannot_bounce_you_off_site(env):
    """开放重定向：站内登录页把人送去钓鱼站，是最省事的一种钓鱼。"""
    assert _walk_in(env, "//evil.example/").headers["location"] == "/"


def test_the_session_cookie_is_not_readable_by_scripts(env):
    assert "httponly" in _walk_in(env).headers["set-cookie"].lower()


# ---------- 一次性 ----------

def test_the_same_state_cannot_be_used_twice(env):
    start = env.client.get("/auth/entra")
    state = re.search(r"state=([^&]+)", start.headers["location"]).group(1)
    env.idp.nonce_seen = re.search(
        r"nonce=([^&]+)", start.headers["location"]).group(1)
    assert env.client.get(
        f"/auth/entra/callback?code=c&state={state}").status_code == 303
    assert env.client.get(
        f"/auth/entra/callback?code=c&state={state}").status_code == 400


def test_a_callback_with_no_state_is_refused(env):
    assert env.client.get("/auth/entra/callback?code=c").status_code == 400


def test_a_made_up_state_is_refused(env):
    """CSRF-on-login：拿别人的 code 配自己的 state，把你登进他的账号。"""
    assert env.client.get(
        "/auth/entra/callback?code=c&state=made-up").status_code == 400


def test_the_error_page_is_readable_not_a_stack_trace(env):
    body = env.client.get("/auth/entra/callback?code=c&state=made-up").text
    assert "Traceback" not in body
    assert "Sign-in did not complete" in body


def test_the_idp_saying_no_is_shown_as_a_sentence(env):
    body = env.client.get(
        "/auth/entra/callback?error=access_denied"
        "&error_description=AADSTS50105").text
    assert "Traceback" not in body
    assert "access_denied" in body or "refused" in body


# ---------- 一个人，两条路 ----------

def test_the_invited_local_account_is_the_same_person(env):
    """先发了邮箱邀请、人却先走了 SSO——不认这条会长出两个同名账号。"""
    local = env.identity.create_account(
        email="ann@acme.cn", password="pw-ann-ann-ann", roles=("approver",))
    _walk_in(env)
    assert len(env.identity.list_accounts()) == 1
    assert env.identity.by_id(local.id) is not None


def test_the_page_says_entra_overwrites_hand_edits(env):
    """不写出来，管理员会以为自己改生效了。设计 §5.4"""
    _walk_in(env)
    env.identity.grant(env.identity.by_email("ann@acme.cn").id, "admin", by="cli")
    page = env.client.get("/members").text
    assert "Entra" in page and "overwritten" in page


# ---------- 部署 ----------

def test_an_https_callback_forces_a_secure_cookie(env):
    """回调地址是 https 就说明这套部署在 https 上。靠部署的人记得传开关，
    漏一次就是会话 cookie 明文过网。"""
    assert "secure" in _walk_in(env).headers["set-cookie"].lower()


def test_a_local_http_deployment_still_gets_a_usable_cookie(tmp_path, monkeypatch):
    """本机 http 调试时带 Secure，cookie 会直接不发——等于登不进去。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "h3"))
    db = tmp_path / "c.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    conn.close()
    from framework_reader.web.app import create_app

    store = IdentityStore()
    store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                         roles=("admin",))
    client = TestClient(create_app(db), follow_redirects=False)
    response = client.post("/login", data={"email": "boss@acme.cn",
                                           "password": "pw-boss-boss"})
    assert "secure" not in response.headers["set-cookie"].lower()
