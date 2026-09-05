"""Storage for accounts, roles, invites, sessions, audit. See the hosted-service
design §2, §4

**Tokens are stored hashed only.** Session tokens and invite tokens are both
"whoever holds it IS you" objects; stored in plaintext, one database leak equals
every session taken over and every invite abused.
"""
import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from framework_reader import sqlite_setup
from framework_reader.identity import DEFAULT_ROLE, ROLES

SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# Sessions: 8 hours absolute, 1 hour idle (design §5.5)
ABSOLUTE_TTL = timedelta(hours=8)
IDLE_TTL = timedelta(hours=1)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
INVITE_TTL = timedelta(days=7)
LOGIN_FAILURE_WINDOW = timedelta(minutes=5)
MAX_LOGIN_FAILURES = 5
# From clicking "sign in with the company account" to Entra sending the person
# back. Ten minutes is long enough for a slow person to type a password plus a
# second factor, and short enough that the one-time state does not lie around
# in the database for a day.
FLOW_TTL = timedelta(minutes=10)


class IdentityError(Exception):
    """A single sentence that can be shown to the user directly."""


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    display_name: str
    status: str
    roles: frozenset[str]

    @property
    def active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class Session:
    token: str          # has a value only on **creation**; afterwards only the hash remains in the store
    account: Account
    csrf: str


def default_path() -> Path:
    from framework_reader import usage

    return usage.home() / "identity.sqlite"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class IdentityStore:
    # The schema script runs once per process per database. The guard opens this
    # database on **every request**; re-running six DDL statements each time is
    # overhead paid for nothing.
    _ready: set[Path] = set()

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        sqlite_setup.prepare(conn)
        if self.path not in IdentityStore._ready:
            conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            conn.commit()
            IdentityStore._ready.add(self.path)
        return conn

    def configured(self) -> bool:
        """Whether the identity system is enabled - decides whether the door is
        locked. Asked on every request, so it must be cheap.

        The test is "has an account **or** an invite was sent", not "has an
        account". Looking only at accounts, the whole workbench stands open to
        everyone between the admin sending the first invite and the invite being
        accepted - irrelevant on localhost, a very real window on a networked
        deployment. The invite page itself is on the whitelist, so locking the
        door at that point does not shut out the invited person.
        """
        conn = self._conn()
        try:
            for table in ("account", "invite"):
                if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return True
            return False
        except sqlite3.OperationalError:
            return False
        finally:
            conn.close()

    # ---------- accounts ----------

    BOOTSTRAP_ROLES = ("admin", "author", "approver")

    def bootstrap(
        self, *, email: str, password: str,
        roles: tuple[str, ...] = BOOTSTRAP_ROLES,
    ) -> Account | None:
        """Create the first operator if nobody is in the store yet.

        Returns None when accounts already exist (idempotent). The first
        person on a deployed box needs admin *and* author/approver —
        admin alone cannot draft or sign, and self-grant is refused.
        """
        if self.list_accounts():
            return None
        return self.create_account(
            email=email, password=password, roles=roles, granted_by="bootstrap",
        )

    def create_account(
        self, *, email: str, password: str = "", display_name: str = "",
        roles: tuple[str, ...] = (DEFAULT_ROLE,), granted_by: str | None = None,
    ) -> Account:
        from framework_reader.identity.passwords import hash_password

        email = email.strip().lower()
        if not email or "@" not in email:
            raise IdentityError(f"that email address looks wrong: {email!r}")
        for role in roles:
            if role not in ROLES:
                raise IdentityError(f"no such role: {role}")

        account_id = str(uuid.uuid4())
        now = _now().isoformat()
        conn = self._conn()
        try:
            try:
                conn.execute(
                    "INSERT INTO account "
                    "(id, email, display_name, password_hash, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'active', ?)",
                    (account_id, email, display_name,
                     hash_password(password) if password else "", now),
                )
            except sqlite3.IntegrityError as exc:
                raise IdentityError(f"{email} already has an account") from exc
            conn.executemany(
                "INSERT INTO membership (account_id, role, granted_by, granted_at) "
                "VALUES (?, ?, ?, ?)",
                [(account_id, role, granted_by, now) for role in roles],
            )
            conn.commit()
        finally:
            conn.close()
        return Account(id=account_id, email=email, display_name=display_name,
                       status="active", roles=frozenset(roles))

    def by_email(self, email: str) -> Account | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM account WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
            return self._account(conn, row) if row else None
        finally:
            conn.close()

    def by_id(self, account_id: str) -> Account | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM account WHERE id = ?", (account_id,)
            ).fetchone()
            return self._account(conn, row) if row else None
        finally:
            conn.close()

    def list_accounts(self) -> list[Account]:
        conn = self._conn()
        try:
            return [
                self._account(conn, row) for row in
                conn.execute("SELECT * FROM account ORDER BY email")
            ]
        finally:
            conn.close()

    def _account(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Account:
        roles = {
            r[0] for r in conn.execute(
                "SELECT role FROM membership WHERE account_id = ?", (row["id"],))
        }
        return Account(id=row["id"], email=row["email"],
                       display_name=row["display_name"], status=row["status"],
                       roles=frozenset(roles))

    # ---------- roles ----------

    def grant(self, account_id: str, role: str, *, by: str | None = None) -> None:
        """`by` is the **account_id of whoever initiated the action** (the CLI
        passes "cli", which is nobody).

        Granting a role to yourself is refused by default (design §4.3): for an
        admin to draft or sign, another admin has to nod. A single-admin
        organization would be blocked by this, so there are two ways around it -
        a switch and the CLI - and using the switch leaves a trail. **Only grant
        is blocked, not revoke** - lowering privilege is not raising it.
        """
        if role not in ROLES:
            raise IdentityError(f"no such role: {role}")
        if by is not None and by == account_id and not self.self_grant_allowed():
            raise IdentityError(
                "You cannot grant roles to yourself. Have another admin do it, "
                'or turn off the "no self role grants" lock on the Members page (that step is written to the audit log).')
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO membership (account_id, role, granted_by, granted_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (account_id, role, by, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def revoke(self, account_id: str, role: str, *, by: str | None = None) -> None:
        """Revoking the last admin is refused - otherwise the first mistake locks
        everyone out.

        Design §4.3. This invariant lives in the **storage layer**, not the route
        layer, because the CLI can revoke roles too.
        """
        conn = self._conn()
        try:
            if role == "admin" and self._admin_count(conn) <= 1:
                raise IdentityError(
                    "This is the last admin; revoking it would leave the system unmanaged. Grant admin to someone else first.")
            conn.execute(
                "DELETE FROM membership WHERE account_id = ? AND role = ?",
                (account_id, role),
            )
            conn.commit()
        finally:
            conn.close()

    def set_status(self, account_id: str, status: str) -> None:
        if status not in ("active", "disabled"):
            raise IdentityError(f"no such status: {status}")
        conn = self._conn()
        try:
            if status == "disabled":
                row = conn.execute(
                    "SELECT 1 FROM membership WHERE account_id = ? AND role = 'admin'",
                    (account_id,),
                ).fetchone()
                if row and self._admin_count(conn) <= 1:
                    raise IdentityError("This is the last admin; deactivating it would leave the system unmanaged.")
            conn.execute("UPDATE account SET status = ? WHERE id = ?",
                         (status, account_id))
            # Deactivation cuts connections immediately: keeping sessions means the deactivation never took effect
            if status == "disabled":
                conn.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
            conn.commit()
        finally:
            conn.close()

    def _admin_count(self, conn: sqlite3.Connection) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM membership m JOIN account a ON a.id = m.account_id "
            "WHERE m.role = 'admin' AND a.status = 'active'"
        ).fetchone()[0]

    # ---------- switches ----------

    def self_grant_allowed(self) -> bool:
        """Defaults to False - the default decides what happens when nobody
        configures anything, and nobody configuring is the norm."""
        return self._setting("allow_self_grant") == "1"

    def set_self_grant(self, allowed: bool, *, by: str | None = None) -> None:
        self._set_setting("allow_self_grant", "1" if allowed else "0", by=by)
        # Turning this lock off must leave a trail, otherwise "who opened the
        # door" is unanswerable. Design §4.3
        self.log("setting.self_grant", actor=by,
                 detail="self role grant allowed" if allowed else "self role grant forbidden")

    def _setting(self, key: str) -> str:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def _set_setting(self, key: str, value: str, *, by: str | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO setting (key, value, set_by, set_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "set_by = excluded.set_by, set_at = excluded.set_at",
                (key, value, by, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- SSO (Entra ID) settings-page configuration: a second source besides
    # ---- environment variables.----
    # Kept in the setting table (key='sso_entra'); the client secret travels the
    # same encryption path as the model API key (FR_SECRET_KEY). Precedence: a
    # configuration saved here and enabled > environment variables; neither
    # configured = single sign-on off.

    def sso_config(self) -> dict | None:
        """The saved configuration. **The secret never leaves the store** - the page only needs to know "a secret is saved"."""
        raw = self._setting("sso_entra")
        if not raw:
            return None
        data = json.loads(raw)
        return {
            "tenant_id": data.get("tenant_id", ""),
            "client_id": data.get("client_id", ""),
            "redirect_uri": data.get("redirect_uri", ""),
            "authority": data.get("authority", ""),
            "enabled": bool(data.get("enabled")),
            "has_secret": bool(data.get("sealed")),
        }

    def sso_secret(self) -> str:
        """Unseal the saved client secret. With no master key configured, or if
        it will not open, raise SecretError and let the caller decide how to
        degrade - nothing is swallowed here."""
        from framework_reader import crypto

        raw = self._setting("sso_entra")
        if not raw:
            return ""
        sealed = json.loads(raw).get("sealed", "")
        return crypto.open_secret(sealed) if sealed else ""

    def save_sso_config(self, *, tenant_id: str, client_id: str,
                        redirect_uri: str, secret: str = "",
                        authority: str = "", enabled: bool = True,
                        by: str | None = None) -> None:
        from framework_reader import crypto

        previous = self._setting("sso_entra")
        sealed = ""
        if secret.strip():
            sealed = crypto.seal(secret.strip())
        elif previous:
            # Leaving the secret blank in the form = keep the saved one, not clear it.
            sealed = json.loads(previous).get("sealed", "")
        self._set_setting("sso_entra", json.dumps({
            "tenant_id": tenant_id.strip(), "client_id": client_id.strip(),
            "sealed": sealed, "redirect_uri": redirect_uri.strip(),
            "authority": authority.strip(), "enabled": bool(enabled),
        }, ensure_ascii=False), by=by)

    def clear_sso_config(self) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM setting WHERE key = 'sso_entra'")
            conn.commit()
        finally:
            conn.close()

    # ---------- invites ----------

    def invite(self, *, email: str, role: str = DEFAULT_ROLE,
               by: str | None = None) -> str:
        """Returns the **plaintext token**, this one time only. Only the hash stays in the store."""
        if role not in ROLES:
            raise IdentityError(f"no such role: {role}")
        email = email.strip().lower()
        if self.by_email(email):
            raise IdentityError(f"{email} already has an account")
        token = secrets.token_urlsafe(32)
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO invite "
                "(token_hash, email, role, created_by, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_digest(token), email, role, by, now.isoformat(),
                 (now + INVITE_TTL).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return token

    def peek_invite(self, token: str) -> dict | None:
        """Check whether an invite is usable without consuming it - the "set
        password" page needs it for rendering."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT email, role, expires_at, used_at FROM invite "
                "WHERE token_hash = ?", (_digest(token),)
            ).fetchone()
        finally:
            conn.close()
        if row is None or row["used_at"]:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            return None
        return {"email": row["email"], "role": row["role"]}

    def accept_invite(self, token: str, *, password: str,
                      display_name: str = "") -> Account:
        pending = self.peek_invite(token)
        if pending is None:
            raise IdentityError("This invitation link is invalid or expired. Ask an admin to send a new one.")
        account = self.create_account(
            email=pending["email"], password=password,
            display_name=display_name or pending["email"].split("@")[0],
            roles=(pending["role"],),
        )
        conn = self._conn()
        try:
            # One-time: voided the moment it is accepted; replaying the same link no longer works
            conn.execute("UPDATE invite SET used_at = ? WHERE token_hash = ?",
                         (_now().isoformat(), _digest(token)))
            conn.commit()
        finally:
            conn.close()
        return account

    # ---------- Entra (design §5) ----------

    def sign_in_entra(self, claims) -> "Session":
        """SSO sign-in: find the person by `oid`; if not found, claim the account
        by email; if still none, create one.

        **The primary key is `oid`.** email changes (renames, domain moves,
        aliases); make it the primary key and a user who renames becomes a new
        person with all history lost. email is used only for display, and for
        claiming the account of someone who "was emailed an invite and went
        through SSO before accepting it" - refuse that and two same-named
        accounts grow, with nobody able to say which one is him.
        """
        account = self.by_entra_oid(claims.oid)
        if account is None:
            account = self.by_email(claims.email)
            if account is None:
                account = self.create_account(
                    email=claims.email, display_name=claims.display_name,
                    roles=tuple(sorted(claims.roles)), granted_by="entra")
                self.log("account.sso_created", actor=claims.email,
                         detail=", ".join(sorted(claims.roles)))
            self._set_entra_oid(account.id, claims.oid)

        if not account.active:
            self.log("login.failed", actor=claims.email, detail="account deactivated")
            raise IdentityError("This account has been deactivated. Contact an admin.")

        self._sync_entra(account, claims)
        account = self.by_id(account.id)
        self.log("login.ok", actor=account.email, detail="entra")
        return self.start_session(account)

    def by_entra_oid(self, oid: str) -> Account | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM account WHERE entra_oid = ?", (oid,)).fetchone()
            return self._account(conn, row) if row else None
        finally:
            conn.close()

    def _set_entra_oid(self, account_id: str, oid: str) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE account SET entra_oid = ? WHERE id = ?",
                         (oid, account_id))
            conn.commit()
        finally:
            conn.close()

    def _sync_entra(self, account: Account, claims) -> None:
        """Entra App Role → membership, **a one-way sync at sign-in only**.

        Manual adjustments made on this side are overwritten the next time that
        user signs in - this rule must be written on the interface, or the admin
        will believe his change took effect.

        The one exception: **the sync never revokes the last admin**. One
        missing App Role assignment on the Entra side and nobody can run the
        system - that must not be a side effect of one login.
        """
        conn = self._conn()
        try:
            if claims.email and claims.email != account.email:
                conn.execute("UPDATE account SET email = ? WHERE id = ?",
                             (claims.email.strip().lower(), account.id))
            if claims.display_name:
                conn.execute("UPDATE account SET display_name = ? WHERE id = ?",
                             (claims.display_name, account.id))
            conn.commit()
        finally:
            conn.close()

        target = frozenset(claims.roles)
        if target == account.roles:
            return
        for role in sorted(target - account.roles):
            self.grant(account.id, role, by="entra")
        for role in sorted(account.roles - target):
            try:
                self.revoke(account.id, role, by="entra")
            except IdentityError:
                # The last admin. Keep the role, and say out loud that this happened.
                self.log("role.sync_refused", actor=account.email,
                         detail=f"Entra did not grant {role}, but revoking it would leave the system unmanaged")
        self.log("role.sync", actor=account.email,
                 detail=f"{', '.join(sorted(account.roles)) or '(none)'} → "
                        f"{', '.join(sorted(target))}")

    # ---------- one-time state during login ----------

    def start_oidc_flow(self, next_url: str = "/") -> tuple[str, str, str]:
        """Returns (state, nonce, verifier). Each exists exactly once and is deleted on first use."""
        from framework_reader.identity.entra import new_verifier

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = new_verifier()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO oidc_flow (state, nonce, verifier, next_url, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (state, nonce, verifier, next_url, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return state, nonce, verifier

    def take_oidc_flow(self, state: str) -> dict | None:
        """Fetch it and **delete it immediately**. Replaying the same state gets nothing a second time."""
        if not state:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM oidc_flow WHERE state = ?", (state,)).fetchone()
            conn.execute("DELETE FROM oidc_flow WHERE state = ?", (state,))
            # Sweep expired rows while here. Nothing else ever scans this table.
            conn.execute("DELETE FROM oidc_flow WHERE created_at < ?",
                         ((_now() - FLOW_TTL).isoformat(),))
            conn.commit()
        finally:
            conn.close()
        if row is None:
            return None
        if datetime.fromisoformat(row["created_at"]) + FLOW_TTL < _now():
            return None
        return {"nonce": row["nonce"], "verifier": row["verifier"],
                "next_url": row["next_url"]}

    # ---------- sessions ----------

    def login(self, email: str, password: str) -> Session:
        from framework_reader.identity.passwords import verify_password

        email = email.strip().lower()
        if self._recent_login_failures(email) >= MAX_LOGIN_FAILURES:
            raise IdentityError("Wrong email or password.")
        account = self.by_email(email)
        conn = self._conn()
        try:
            stored = ""
            if account is not None:
                row = conn.execute(
                    "SELECT password_hash FROM account WHERE id = ?", (account.id,)
                ).fetchone()
                stored = row["password_hash"] if row else ""
        finally:
            conn.close()

        # Unknown email and wrong password get the same message at the same cost -
        # telling them apart is an account-enumeration interface.
        ok = bool(stored) and verify_password(password, stored)
        if account is None or not account.active or not ok:
            self.log("login.failed", actor=email, detail="wrong email or password")
            raise IdentityError("Wrong email or password.")
        self.log("login.ok", actor=account.email)
        return self.start_session(account)

    def _recent_login_failures(self, email: str) -> int:
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE event = 'login.failed' AND actor = ? AND at >= ?",
                (email, (_now() - LOGIN_FAILURE_WINDOW).isoformat()),
            ).fetchone()[0]
        finally:
            conn.close()

    def start_session(self, account: Account) -> Session:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO session "
                "(id, account_id, csrf, created_at, last_seen, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_digest(token), account.id, csrf, now.isoformat(), now.isoformat(),
                 (now + ABSOLUTE_TTL).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return Session(token=token, account=account, csrf=csrf)

    def resume(self, token: str) -> Session | None:
        """Fetch the session by token and advance last_seen. Expired ones are deleted in place."""
        if not token:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM session WHERE id = ?", (_digest(token),)
            ).fetchone()
            if row is None:
                return None
            now = _now()
            expired = (
                datetime.fromisoformat(row["expires_at"]) < now
                or datetime.fromisoformat(row["last_seen"]) + IDLE_TTL < now
            )
            if expired:
                conn.execute("DELETE FROM session WHERE id = ?", (row["id"],))
                conn.commit()
                return None
            if datetime.fromisoformat(row["last_seen"]) + SESSION_TOUCH_INTERVAL < now:
                conn.execute("UPDATE session SET last_seen = ? WHERE id = ?",
                             (now.isoformat(), row["id"]))
                conn.commit()
            account_row = conn.execute(
                "SELECT * FROM account WHERE id = ?", (row["account_id"],)
            ).fetchone()
            if account_row is None or account_row["status"] != "active":
                return None
            account = self._account(conn, account_row)
        finally:
            conn.close()
        return Session(token="", account=account, csrf=row["csrf"])

    def logout(self, token: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM session WHERE id = ?", (_digest(token),))
            conn.commit()
        finally:
            conn.close()

    # ---------- audit ----------

    def log(self, event: str, *, actor: str | None = None, detail: str = "") -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO audit_log (at, actor, event, detail) VALUES (?, ?, ?, ?)",
                (_now().isoformat(), actor, event, detail),
            )
            conn.commit()
        finally:
            conn.close()

    def audit(self, limit: int = 100) -> list[dict]:
        conn = self._conn()
        try:
            return [
                dict(r) for r in conn.execute(
                    "SELECT at, actor, event, detail FROM audit_log "
                    "ORDER BY id DESC LIMIT ?", (limit,))
            ]
        finally:
            conn.close()
