"""账号、角色、邀请、会话、审计的存储。见网页服务化设计 §2、§4

**令牌只存哈希。** 会话令牌与邀请令牌都是「拿到就等于是你」的东西，
明文落库的话，一次库泄漏就等于所有会话被接管、所有邀请被冒用。
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

# 会话：绝对 8 小时、空闲 1 小时（设计 §5.5）
ABSOLUTE_TTL = timedelta(hours=8)
IDLE_TTL = timedelta(hours=1)
INVITE_TTL = timedelta(days=7)
# 从点「用公司账号登录」到 Entra 把人送回来。十分钟够慢的人输密码加二次验证，
# 又短到那串一次性状态不会在库里躺一天。
FLOW_TTL = timedelta(minutes=10)


class IdentityError(Exception):
    """能直接给用户看的一句话。"""


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
    token: str          # 只在**新建**时有值，之后库里只剩哈希
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
    # 建表脚本每个进程对每个库只跑一次。守卫在**每个请求**上都要开这个库，
    # 每次重跑六条 DDL 是白付的开销。
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
        """身份体系启用了没有——决定门锁不锁。每个请求都问一次，所以要便宜。

        判据是「有账号**或**发过邀请」，不是「有账号」。只看账号的话，
        从管理员发出第一个邀请、到对方接受之间，整个工作台对所有人敞开——
        本机无所谓，联网部署就是一个实打实的窗口。邀请页本身在白名单里，
        所以这时锁门不会把被邀请的人挡在外面。
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

    # ---------- 账号 ----------

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

    # ---------- 角色 ----------

    def grant(self, account_id: str, role: str, *, by: str | None = None) -> None:
        """`by` 是**动作发起人的 account_id**（CLI 传 "cli"，那不是任何人）。

        自己给自己加角色默认被拒（设计 §4.3）：admin 要起草或签字，得另一个
        admin 点头。单 admin 的组织会被这条挡住，所以留了开关和 CLI 两条路，
        而走开关要留痕。**只挡 grant 不挡 revoke**——降权不是提权。
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
        """撤销最后一个 admin 会被拒绝——否则第一次误操作就把所有人锁在门外。

        设计 §4.3。这条不变量在**存储层**而不是路由层，因为 CLI 也能撤角色。
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
            # 停用即刻断线：留着会话等于停用没生效
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

    # ---------- 开关 ----------

    def self_grant_allowed(self) -> bool:
        """默认 False——默认值决定了没人配置时会发生什么，而没人配置是常态。"""
        return self._setting("allow_self_grant") == "1"

    def set_self_grant(self, allowed: bool, *, by: str | None = None) -> None:
        self._set_setting("allow_self_grant", "1" if allowed else "0", by=by)
        # 关掉这道锁必须留痕，否则「谁把门打开的」无从查起。设计 §4.3
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

    # ---- SSO（Entra ID）的设置页配置：环境变量之外的第二个来源。----
    # 存在 setting 表（key='sso_entra'）里，client secret 与模型 API key
    # 走同一条加密路径（FR_SECRET_KEY）。生效优先级：这里保存且启用的
    # 配置 > 环境变量；两处都没配 = 单点登录关闭。

    def sso_config(self) -> dict | None:
        """已保存的配置。**secret 不出库**——页面只需要知道「已保存」。"""
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
        """解出已保存的 client secret。没配主密钥或解不开就抛
        SecretError，由调用方决定怎么降级——这里不吞。"""
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
            # 表单里 secret 留空 = 保留已存的那份，不是清掉。
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

    # ---------- 邀请 ----------

    def invite(self, *, email: str, role: str = DEFAULT_ROLE,
               by: str | None = None) -> str:
        """返回**明文令牌**，只此一次。库里只留哈希。"""
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
        """看邀请是否可用，不消费它——渲染「设置口令」那一页要用。"""
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
            # 一次性：接受之后立刻作废，重放同一个链接不再有效
            conn.execute("UPDATE invite SET used_at = ? WHERE token_hash = ?",
                         (_now().isoformat(), _digest(token)))
            conn.commit()
        finally:
            conn.close()
        return account

    # ---------- Entra（设计 §5） ----------

    def sign_in_entra(self, claims) -> "Session":
        """SSO 登录：按 `oid` 找人，找不到就按 email 认领，再没有就建号。

        **主键是 `oid`。** email 会变（改名、换域、别名），拿它做主键，
        用户改个名就成了新人、丢掉全部历史。email 只用于显示，以及
        认领那个「先发了邮箱邀请、人还没接受就先走了 SSO」的账号——
        不认这条会长出两个同名账号，而谁都说不清哪个是他。
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
        """Entra App Role → membership，**只在登录时单向同步**。

        这边的手工调整会在该用户下次登录时被覆盖——这一条必须写在界面上，
        不然管理员会以为自己改生效了。

        唯一的例外：**同步不会撤掉最后一个 admin**。Entra 那边少配一个
        App Role，就没人能管系统了——那不能是一次登录的副作用。
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
                # 最后一个 admin。留着，并把这件事说出来。
                self.log("role.sync_refused", actor=account.email,
                         detail=f"Entra did not grant {role}, but revoking it would leave the system unmanaged")
        self.log("role.sync", actor=account.email,
                 detail=f"{', '.join(sorted(account.roles)) or '(none)'} → "
                        f"{', '.join(sorted(target))}")

    # ---------- 登录途中的一次性状态 ----------

    def start_oidc_flow(self, next_url: str = "/") -> tuple[str, str, str]:
        """返回 (state, nonce, verifier)。三样都只此一份，用一次即删。"""
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
        """取出并**立刻删掉**。重放同一个 state 拿不到第二次。"""
        if not state:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM oidc_flow WHERE state = ?", (state,)).fetchone()
            conn.execute("DELETE FROM oidc_flow WHERE state = ?", (state,))
            # 顺手清掉过期的。没有别的地方会去扫这张表。
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

    # ---------- 会话 ----------

    def login(self, email: str, password: str) -> Session:
        from framework_reader.identity.passwords import verify_password

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

        # 邮箱不存在与口令不对给同一句话、走同样的代价——
        # 区别开就是一个账号枚举接口。
        ok = bool(stored) and verify_password(password, stored)
        if account is None or not account.active or not ok:
            self.log("login.failed", actor=email, detail="wrong email or password")
            raise IdentityError("Wrong email or password.")
        self.log("login.ok", actor=account.email)
        return self.start_session(account)

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
        """按令牌取回会话，并推进 last_seen。过期的就地删掉。"""
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

    # ---------- 审计 ----------

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
