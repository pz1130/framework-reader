"""Entra ID（AAD）接入：OIDC Authorization Code + PKCE。见网页服务化设计 §5

**这个模块只做一件事：把一串 id_token 变成「这是谁、他有什么角色」。**
它不碰会话、不碰数据库——那是 `IdentityStore` 的事。分开是因为
校验的每一条都得能单独被测试盯住（`tests/identity/test_entra.py`）。

三条不肯让步的：

- **端点走发现文档，不硬编码。** 硬编码会在对方改路径的那天全线崩，
  而且崩得莫名其妙。
- **§5.2 那张表一条都不能省。** 签名、iss、aud、exp/nbf、nonce、tid——
  每一条少了都能被冒充。尤其 `tid`：应用注册配成多租户的话，
  **任何** Entra 用户都能走到你的回调。
- **主键用 `oid`。** email / upn / preferred_username 都会变（改名、换域、
  别名），拿它们做主键，用户改个名就成了新人、丢掉全部历史。
"""
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from framework_reader.identity import DEFAULT_ROLE, ROLES

# 时钟偏移容忍。5 分钟是设计 §5.2 定的上限，不是起点。
CLOCK_SKEW = 300

# 未知 kid 触发的 JWKS 刷新，至少间隔这么久。
# 认不出的 kid 就刷一次；刷到底等于给了对方一个放大器。
JWKS_MIN_REFRESH = 60


class EntraError(Exception):
    """能直接给用户看的一句话。细节进日志，不进页面。"""


@dataclass(frozen=True)
class EntraClaims:
    oid: str
    email: str
    display_name: str
    roles: frozenset[str]


@dataclass(frozen=True)
class EntraConfig:
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    authority: str = "https://login.microsoftonline.com"

    def configured(self) -> bool:
        return bool(self.tenant_id and self.client_id)

    @property
    def discovery_url(self) -> str:
        return (f"{self.authority.rstrip('/')}/{self.tenant_id}"
                "/v2.0/.well-known/openid-configuration")

    @classmethod
    def from_env(cls) -> "EntraConfig":
        return cls(
            tenant_id=os.getenv("FR_ENTRA_TENANT_ID", ""),
            client_id=os.getenv("FR_ENTRA_CLIENT_ID", ""),
            client_secret=os.getenv("FR_ENTRA_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("FR_ENTRA_REDIRECT_URI", ""),
            authority=os.getenv("FR_ENTRA_AUTHORITY",
                                "https://login.microsoftonline.com"),
        )


def new_verifier() -> str:
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    """S256。明文送 verifier 等于没做 PKCE。"""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _default_fetch(method: str, url: str, data=None):
    import httpx

    with httpx.Client(timeout=10.0) as client:
        response = client.request(method, url, data=data)
        response.raise_for_status()
        return response.json()


class EntraClient:
    """`fetch(method, url, data=None) -> dict` 可注入，测试拿它当假 IdP。"""

    def __init__(self, config: EntraConfig, fetch=None) -> None:
        self.config = config
        self._fetch = fetch or _default_fetch
        self._discovery: dict | None = None
        self._keys: dict[str, dict] = {}
        # 只记「因为认不出 kid 而刷新」的时刻。None = 还没追过。
        # 不能写成 0.0：CI 容器的 monotonic 从开机起跳，前 60 秒
        # `now - 0 > JWKS_MIN_REFRESH` 为假，轮换后的第一张令牌会被节流挡住。
        self._chased_at: float | None = None

    # ---------- 发现 ----------

    def discovery(self) -> dict:
        if self._discovery is None:
            try:
                self._discovery = self._fetch("GET", self.config.discovery_url)
            except Exception as exc:
                raise EntraError("Cannot reach the company login service. Try again later, or sign in with your email and password.") from exc
        return self._discovery

    def _load_keys(self) -> None:
        document = self._fetch("GET", self.discovery()["jwks_uri"])
        self._keys = {k["kid"]: k for k in document.get("keys", []) if k.get("kid")}

    def _key_for(self, kid: str) -> dict:
        if not self._keys:
            self._load_keys()
        if kid not in self._keys and (
                self._chased_at is None
                or time.monotonic() - self._chased_at > JWKS_MIN_REFRESH):
            # 微软会轮换签名密钥，缓存跟不上轮换 = 某天早上全员登不进来；
            # 而每张认不出的令牌都去刷一次，等于给了对方一个放大器。
            # 所以：追一次，然后按 JWKS_MIN_REFRESH 节流。
            self._chased_at = time.monotonic()
            self._load_keys()
        if kid not in self._keys:
            raise EntraError("This token was signed with a key we do not recognize.")
        return self._keys[kid]

    # ---------- 授权请求 ----------

    def authorize_url(self, *, state: str, nonce: str, challenge: str) -> str:
        query = urlencode({
            "client_id": self.config.client_id,
            # code，不是 id_token：implicit 已废弃
            "response_type": "code",
            "redirect_uri": self.config.redirect_uri,
            "response_mode": "query",
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{self.discovery()['authorization_endpoint']}?{query}"

    def exchange(self, *, code: str, verifier: str) -> dict:
        try:
            return self._fetch("POST", self.discovery()["token_endpoint"], data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": verifier,
            })
        except EntraError:
            raise
        except Exception as exc:
            raise EntraError("Token exchange with the company login service failed. Go back to the login page and try again.") from exc

    # ---------- 校验 ----------

    def verify_id_token(self, raw: str, *, nonce: str) -> EntraClaims:
        import jwt

        try:
            header = jwt.get_unverified_header(raw)
        except Exception as exc:
            raise EntraError("This token is not a readable JWT.") from exc
        kid = header.get("kid") or ""
        if header.get("alg") not in ("RS256", "RS384", "RS512"):
            # alg=none 是 JWT 最老的那个洞；对称算法在这里也没有道理。
            raise EntraError("This token is signed with the wrong algorithm.")
        key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(self._key_for(kid)))

        try:
            claims = jwt.decode(
                raw, key=key, algorithms=["RS256", "RS384", "RS512"],
                audience=self.config.client_id,
                issuer=self.discovery()["issuer"],
                leeway=CLOCK_SKEW,
                options={"require": ["exp", "aud", "iss"]},
            )
        except Exception as exc:
            raise EntraError("This login token failed validation. Go back to the login page and try again.") from exc

        # nonce 与 tid 得自己判——PyJWT 不认识它们。
        if not nonce or claims.get("nonce") != nonce:
            raise EntraError("The one-time nonce for this login did not match; rejected.")
        if claims.get("tid") != self.config.tenant_id:
            # 单组织形态下这一条就是那扇门本身。
            raise EntraError("This account does not belong to this company's directory.")

        oid = claims.get("oid") or ""
        if not oid:
            raise EntraError("This token has no oid claim; the user cannot be identified.")

        return EntraClaims(
            oid=oid,
            email=(claims.get("preferred_username") or claims.get("email") or ""),
            display_name=claims.get("name") or "",
            roles=roles_from(claims.get("roles")),
        )


def roles_from(claim) -> frozenset[str]:
    """App Role → 我们的四个角色。认不出的一概忽略。

    用 `roles` 不用 `groups`（设计 §5.4）：groups 有 200 个的溢出限制，
    超了会变成 `_claim_names` 逼你回头调 Graph；而且组名是客户 AD 的
    内部约定，和我们的角色不是一回事。

    **一个都认不出时给 `viewer`。** 给空集的话这个人登录成功却处处 403，
    看起来像系统坏了；给只读则是「配置还没做完」的正确形状。
    """
    known = {r for r in (claim or []) if r in ROLES}
    return frozenset(known or {DEFAULT_ROLE})
