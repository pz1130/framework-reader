"""Entra ID（AAD）接入。见 2026-08-23 网页服务化设计 §5

**这里没有一次真实出网。** 测试自己当 IdP：本地生成一对 RSA 密钥、
自己签 id_token、自己发 JWKS，注入一个假的 fetch。没有这一层，
「签名校验真的在跑吗」只能靠读代码来相信。

§5.2 那张表里的每一条校验，这里都有一条对应的测试——**每一条都是
少了就能被冒充的**，所以没有一条可以「以后再说」。
"""
import json
import time
from dataclasses import dataclass, field

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from framework_reader.identity.entra import (
    CLOCK_SKEW, EntraClient, EntraConfig, EntraError, challenge_for,
)

TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-9999-9999-9999-999999999999"
CLIENT_ID = "app-client-id"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


@dataclass
class FakeEntra:
    """一个只有一把钥匙的 IdP。"""

    key: object = field(default_factory=lambda: rsa.generate_private_key(
        public_exponent=65537, key_size=2048))
    kid: str = "kid-1"
    jwks_fetches: int = 0
    exchanged: list = field(default_factory=list)
    token_to_return: str = ""

    def jwk(self) -> dict:
        raw = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        raw.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return raw

    def sign(self, *, kid: str | None = None, **claims) -> str:
        payload = {
            "iss": ISSUER, "aud": CLIENT_ID, "tid": TENANT,
            "oid": "oid-of-ann", "sub": "subject",
            "preferred_username": "ann@acme.cn", "name": "安然",
            "iat": int(time.time()), "nbf": int(time.time()),
            "exp": int(time.time()) + 600, "nonce": "the-nonce",
        }
        payload.update(claims)
        for key, value in list(payload.items()):
            if value is None:
                del payload[key]
        return jwt.encode(payload, self.key, algorithm="RS256",
                          headers={"kid": kid or self.kid})

    def fetch(self, method: str, url: str, data=None):
        if url.endswith("openid-configuration"):
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/keys",
            }
        if url.endswith("/keys"):
            self.jwks_fetches += 1
            return {"keys": [self.jwk()]}
        if url.endswith("/token"):
            self.exchanged.append(data)
            return {"id_token": self.token_to_return, "token_type": "Bearer"}
        raise AssertionError(f"没料到会请求 {url}")


@pytest.fixture(scope="module")
def idp():
    return FakeEntra()


@pytest.fixture
def client(idp):
    idp.jwks_fetches = 0
    idp.kid = "kid-1"
    return EntraClient(
        EntraConfig(tenant_id=TENANT, client_id=CLIENT_ID,
                    client_secret="shh", redirect_uri="https://fr.acme.cn/auth/entra/callback"),
        fetch=idp.fetch,
    )


# ---------- 配置 ----------

def test_entra_is_off_until_it_is_configured():
    assert EntraConfig(tenant_id="", client_id="").configured() is False


def test_the_endpoints_come_from_the_discovery_document(client):
    """硬编码端点会在微软改路径的那天全线崩，而且崩得莫名其妙。"""
    assert client.discovery()["token_endpoint"] == f"{ISSUER}/token"


# ---------- 授权请求 ----------

def test_the_authorize_url_carries_pkce_and_a_nonce(client):
    url = client.authorize_url(state="st", nonce="no", challenge="ch")
    for expected in ("code_challenge=ch", "code_challenge_method=S256",
                     "response_type=code", "state=st", "nonce=no",
                     f"client_id={CLIENT_ID}"):
        assert expected in url


def test_the_challenge_is_the_hash_not_the_verifier():
    """明文送 verifier 等于没做 PKCE。"""
    verifier = "a" * 64
    assert challenge_for(verifier) != verifier
    assert "=" not in challenge_for(verifier)      # base64url 不带填充


def test_we_never_ask_for_implicit(client):
    url = client.authorize_url(state="st", nonce="no", challenge="ch")
    assert "id_token" not in url.split("response_type=")[1].split("&")[0]


# ---------- §5.2 一条都不能省 ----------

def test_a_good_token_passes(client, idp):
    claims = client.verify_id_token(idp.sign(), nonce="the-nonce")
    assert claims.oid == "oid-of-ann"
    assert claims.email == "ann@acme.cn"


def test_a_token_signed_by_someone_else_is_refused(client):
    other = FakeEntra()
    with pytest.raises(EntraError):
        client.verify_id_token(other.sign(), nonce="the-nonce")


def test_an_unsigned_token_is_refused(client, idp):
    """alg=none 是 JWT 最老的那个洞。"""
    raw = jwt.encode({"iss": ISSUER, "aud": CLIENT_ID, "tid": TENANT,
                      "oid": "x", "exp": int(time.time()) + 600,
                      "nonce": "the-nonce"}, key="", algorithm="none")
    with pytest.raises(EntraError):
        client.verify_id_token(raw, nonce="the-nonce")


def test_a_token_for_another_app_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(aud="someone-elses-app"),
                               nonce="the-nonce")


def test_a_token_from_another_issuer_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(iss="https://evil.example/v2.0"),
                               nonce="the-nonce")


def test_a_token_from_another_tenant_is_refused(client, idp):
    """应用注册配成多租户的话，任何 Entra 用户都能走到你的回调。

    单组织形态下这一条就是那扇门本身。
    """
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(tid=OTHER_TENANT), nonce="the-nonce")


def test_an_expired_token_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(
            idp.sign(exp=int(time.time()) - CLOCK_SKEW - 60), nonce="the-nonce")


def test_a_token_from_the_future_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(
            idp.sign(nbf=int(time.time()) + CLOCK_SKEW + 60,
                     exp=int(time.time()) + 3600), nonce="the-nonce")


def test_a_few_seconds_of_clock_drift_is_tolerated(client, idp):
    claims = client.verify_id_token(
        idp.sign(nbf=int(time.time()) + 30, exp=int(time.time()) + 3600),
        nonce="the-nonce")
    assert claims.oid == "oid-of-ann"


def test_a_replayed_token_with_the_wrong_nonce_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(), nonce="a-different-nonce")


def test_a_token_with_no_nonce_at_all_is_refused(client, idp):
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(nonce=None), nonce="the-nonce")


def test_a_token_with_no_oid_is_refused(client, idp):
    """oid 是主键。没有它就没有「这个人是谁」。"""
    with pytest.raises(EntraError):
        client.verify_id_token(idp.sign(oid=None), nonce="the-nonce")


# ---------- 密钥轮换 ----------

def test_an_unknown_kid_refreshes_the_key_set_once(client, idp):
    client.verify_id_token(idp.sign(), nonce="the-nonce")     # 先热一遍缓存
    before = idp.jwks_fetches
    idp.kid = "kid-2"                                          # 微软轮换了
    client.verify_id_token(idp.sign(), nonce="the-nonce")
    assert idp.jwks_fetches == before + 1


def test_a_bogus_kid_does_not_hammer_the_key_endpoint(client, idp):
    """认不出的 kid 就刷一次。刷到底等于给了对方一个放大器。"""
    client.verify_id_token(idp.sign(), nonce="the-nonce")
    before = idp.jwks_fetches
    for _ in range(5):
        with pytest.raises(EntraError):
            client.verify_id_token(idp.sign(kid="made-up"), nonce="the-nonce")
    assert idp.jwks_fetches <= before + 1


def test_the_key_set_is_not_refetched_for_every_token(client, idp):
    for _ in range(3):
        client.verify_id_token(idp.sign(), nonce="the-nonce")
    assert idp.jwks_fetches == 1


# ---------- 角色 ----------

def test_app_roles_come_across(client, idp):
    claims = client.verify_id_token(
        idp.sign(roles=["author", "approver"]), nonce="the-nonce")
    assert claims.roles == frozenset({"author", "approver"})


def test_a_role_we_do_not_have_is_ignored_not_a_crash(client, idp):
    claims = client.verify_id_token(
        idp.sign(roles=["author", "SecurityTeam-Lead"]), nonce="the-nonce")
    assert claims.roles == frozenset({"author"})


def test_no_app_role_means_read_only(client, idp):
    """默认值决定了配置忘了做的时候会发生什么，而忘记配置是常态。"""
    claims = client.verify_id_token(idp.sign(), nonce="the-nonce")
    assert claims.roles == frozenset({"viewer"})


def test_groups_are_not_used_as_roles(client, idp):
    """groups 有 200 个的溢出限制，而且组名是客户 AD 的内部约定。设计 §5.4"""
    claims = client.verify_id_token(
        idp.sign(groups=["admin", "author"]), nonce="the-nonce")
    assert claims.roles == frozenset({"viewer"})


# ---------- 换码 ----------

def test_the_exchange_sends_the_verifier_and_the_secret(client, idp):
    idp.token_to_return = idp.sign()
    client.exchange(code="the-code", verifier="the-verifier")
    sent = idp.exchanged[-1]
    assert sent["code"] == "the-code"
    assert sent["code_verifier"] == "the-verifier"
    assert sent["client_secret"] == "shh"
    assert sent["grant_type"] == "authorization_code"
