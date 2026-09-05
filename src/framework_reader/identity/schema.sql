-- Identity layer. See the 2026-08-23 hosted-service design §2, §4
--
-- **Physically separate** from the user store (user.sqlite), for three reasons:
--   1. Sessions are written on every sign-in; kept together, the two sides
--      fight over each other's write locks;
--   2. Exporting/backing up "our compliance material" must not carry password
--      hashes and sessions out along with it;
--   3. A clean boundary: everything here is operations data, everything there
--      is business data.
--
-- Tokens are always **stored hashed only**. A database leak does not mean a
-- usable session or a usable invite.

CREATE TABLE IF NOT EXISTS account (
    id            TEXT PRIMARY KEY,          -- internal primary key. email changes, cannot be the key
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',  -- empty = this account can only sign in via SSO
    entra_oid     TEXT UNIQUE,               -- used by S3; NULL for local accounts
    status        TEXT NOT NULL DEFAULT 'active',   -- active | disabled
    created_at    TEXT NOT NULL
);

-- Roles. One person may hold several; permissions are the union - not an
-- inheritance tree (design §1.1)
CREATE TABLE IF NOT EXISTS membership (
    account_id TEXT NOT NULL REFERENCES account(id),
    role       TEXT NOT NULL,
    granted_by TEXT,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (account_id, role)
);

-- Invites. The first admin needs a way in, and there must be a path when Entra is misconfigured
CREATE TABLE IF NOT EXISTS invite (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

-- Server-side sessions. Opaque tokens, revocable server-side immediately - that is the reason for not using JWT-in-cookie
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,   -- hash of the token, not the token itself
    account_id TEXT NOT NULL REFERENCES account(id),
    csrf       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_account ON session(account_id);

-- Audit log. A compliance product does not stand without it (design §4.4). Append-only: no edits, no deletes
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    actor  TEXT,
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_audit_login_failures
ON audit_log(event, actor, at);

-- Operational switches. Currently just one: allow_self_grant (design §4.3)
CREATE TABLE IF NOT EXISTS setting (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    set_by  TEXT,
    set_at  TEXT NOT NULL
);

-- One-time state during login: PKCE verifier and nonce (design §5.1, §5.2).
-- Kept server-side rather than in a cookie: cookies get carried to other
-- places, and a leak of any one of these three assembles a sign-in.
-- Deleted on first use.
CREATE TABLE IF NOT EXISTS oidc_flow (
    state      TEXT PRIMARY KEY,
    nonce      TEXT NOT NULL,
    verifier   TEXT NOT NULL,
    next_url   TEXT NOT NULL DEFAULT '/',
    created_at TEXT NOT NULL
);
