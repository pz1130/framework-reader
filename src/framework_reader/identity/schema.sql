-- 身份层。见 2026-08-23 网页服务化设计 §2、§4
--
-- 与用户库（user.sqlite）**物理分开**，三条理由：
--   1. 会话每次登录都写，放一起会和业务写锁互相顶；
--   2. 导出/备份「我们的合规材料」不该顺带把口令哈希和会话带出去；
--   3. 边界清楚：这里全是运营数据，那边全是业务数据。
--
-- 令牌一律**只存哈希**。库泄漏不等于会话可用、邀请可用。

CREATE TABLE IF NOT EXISTS account (
    id            TEXT PRIMARY KEY,          -- 内部主键。email 会变，不能当主键
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',  -- 空 = 这个账号只能走 SSO
    entra_oid     TEXT UNIQUE,               -- S3 用；本地账号为 NULL
    status        TEXT NOT NULL DEFAULT 'active',   -- active | disabled
    created_at    TEXT NOT NULL
);

-- 角色。一个人可以多角色，权限取并集——不是继承树（设计 §1.1）
CREATE TABLE IF NOT EXISTS membership (
    account_id TEXT NOT NULL REFERENCES account(id),
    role       TEXT NOT NULL,
    granted_by TEXT,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (account_id, role)
);

-- 邀请。首个 admin 得有办法进来，Entra 配错时也得有条路
CREATE TABLE IF NOT EXISTS invite (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

-- 服务端会话。不透明令牌，能在服务端立刻撤销——这是不用 JWT-in-cookie 的理由
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,   -- 令牌的哈希，不是令牌本身
    account_id TEXT NOT NULL REFERENCES account(id),
    csrf       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_account ON session(account_id);

-- 审计日志。合规产品没有它是不成立的（设计 §4.4）。只追加，不改不删
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    actor  TEXT,
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

-- 运营开关。目前只有一条：allow_self_grant（设计 §4.3）
CREATE TABLE IF NOT EXISTS setting (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    set_by  TEXT,
    set_at  TEXT NOT NULL
);

-- 登录途中的一次性状态：PKCE verifier 与 nonce（设计 §5.1、§5.2）。
-- 放服务端而不是 cookie：cookie 会被带到别的地方去，而这三样每一样
-- 泄漏都能拼出一次登录。用一次即删。
CREATE TABLE IF NOT EXISTS oidc_flow (
    state      TEXT PRIMARY KEY,
    nonce      TEXT NOT NULL,
    verifier   TEXT NOT NULL,
    next_url   TEXT NOT NULL DEFAULT '/',
    created_at TEXT NOT NULL
);
