"""Entra ID (AAD) integration: OIDC Authorization Code + PKCE. See the
hosted-service design §5

**This module does exactly one thing: turn an id_token into "who is this and
what roles does he have".** It does not touch sessions or the database - that is
`IdentityStore`'s business. Kept apart because every single validation rule here
must be individually watchable by a test (`tests/identity/test_entra.py`).

Three things it will not compromise on:

- **Endpoints come from the discovery document, not hardcoded.** Hardcoding
  collapses across the board the day the other side changes a path, and it
  fails in the most baffling way.
- **Not one check from the §5.2 table may be skipped.** Signature, iss, aud,
  exp/nbf, nonce, tid - missing any one of them allows impersonation. `tid`
  above all: if the app registration is configured multi-tenant, **any** Entra
  user can reach your callback.
- **The primary key is `oid`.** email / upn / preferred_username all change
  (renames, domain moves, aliases); make any of them the key and a user who
  renames becomes a new person with all history lost.
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

# Clock-skew tolerance. 5 minutes is the ceiling set by design §5.2, not a starting point.
CLOCK_SKEW = 300

# Minimum interval between JWKS refreshes triggered by an unknown kid.
# An unrecognized kid earns one refresh; refreshing every time hands the other
# side an amplifier.
JWKS_MIN_REFRESH = 60


class EntraError(Exception):
    """A single sentence that can be shown to the user directly. Details go to the log, not the page."""


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
    """S256. Sending the verifier in plaintext amounts to no PKCE at all."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _default_fetch(method: str, url: str, data=None):
    import httpx

    with httpx.Client(timeout=10.0) as client:
        response = client.request(method, url, data=data)
        response.raise_for_status()
        return response.json()


class EntraClient:
    """`fetch(method, url, data=None) -> dict` is injectable; tests use it as a fake IdP."""

    def __init__(self, config: EntraConfig, fetch=None) -> None:
        self.config = config
        self._fetch = fetch or _default_fetch
        self._discovery: dict | None = None
        self._keys: dict[str, dict] = {}
        # Record only the moment of "refreshed because of an unrecognized kid".
        # None = never chased yet. Must not be 0.0: in a CI container the
        # monotonic clock starts at boot, so for the first 60 seconds
        # `now - 0 > JWKS_MIN_REFRESH` is false and the first token after a
        # rotation would be blocked by the throttle.
        self._chased_at: float | None = None

    # ---------- discovery ----------

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
            # Microsoft rotates signing keys; a cache that cannot keep up with
            # rotation means one morning nobody can sign in. But refreshing on
            # every unrecognized token hands the other side an amplifier.
            # So: chase once, then throttle by JWKS_MIN_REFRESH.
            self._chased_at = time.monotonic()
            self._load_keys()
        if kid not in self._keys:
            raise EntraError("This token was signed with a key we do not recognize.")
        return self._keys[kid]

    # ---------- authorization request ----------

    def authorize_url(self, *, state: str, nonce: str, challenge: str) -> str:
        query = urlencode({
            "client_id": self.config.client_id,
            # code, not id_token: implicit flow is deprecated
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

    # ---------- validation ----------

    def verify_id_token(self, raw: str, *, nonce: str) -> EntraClaims:
        import jwt

        try:
            header = jwt.get_unverified_header(raw)
        except Exception as exc:
            raise EntraError("This token is not a readable JWT.") from exc
        kid = header.get("kid") or ""
        if header.get("alg") not in ("RS256", "RS384", "RS512"):
            # alg=none is the oldest hole in JWT; symmetric algorithms make no sense here either.
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

        # nonce and tid must be judged by hand - PyJWT does not know them.
        if not nonce or claims.get("nonce") != nonce:
            raise EntraError("The one-time nonce for this login did not match; rejected.")
        if claims.get("tid") != self.config.tenant_id:
            # In the single-organization setup, this check IS the door.
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
    """App Role → our four roles. Anything unrecognized is ignored.

    `roles` rather than `groups` (design §5.4): groups has a 200-claim overflow
    limit - exceed it and they turn into `_claim_names`, forcing you back to
    Graph calls; and group names are the customer AD's internal convention, not
    the same thing as our roles.

    **When nothing is recognized, hand out `viewer`.** An empty set means the
    person signs in successfully yet gets 403 everywhere, which looks like the
    system is broken; read-only is the correct shape of "configuration not
    finished yet".
    """
    known = {r for r in (claim or []) if r in ROLES}
    return frozenset(known or {DEFAULT_ROLE})
