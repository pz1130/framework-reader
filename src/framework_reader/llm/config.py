"""Admin-configured models, keys, rate limits, and budgets. See the web service design §6⑤⑥

In a local deployment all of this lives in environment variables - read once and done. Going hosted changed two things:

- **Keys are stored on our server.** So they are encrypted at rest, echoed back masked only, and never appear in
  logs or exceptions. Without a master key (`FR_SECRET_KEY`) writes are refused - silently storing plaintext
  is the one failure mode this module will not accept.
- **Drafting spends the organization's money.** A spending button with no cap means the first slip of a hand
  burns a month's budget. Hence three gates: per person per hour, per organization per month, and concurrent jobs.

**The ledger counts items, not currency.** We have no live vendor pricing; forcing a money conversion
creates an illusion of precision. "How many drafts this month" is countable - and the only honest measure.

**Charge when the job starts, not when it finishes.** Charging on completion would make a job that dies halfway
cost nothing on the books - but the money is already gone. Better to over-count the failures.
"""
import sqlite3
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from framework_reader import crypto, sqlite_setup

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_key (
    provider TEXT PRIMARY KEY,
    sealed   TEXT NOT NULL,      -- ciphertext. The master key lives in FR_SECRET_KEY, not in this table
    masked   TEXT NOT NULL,      -- for display only, sk-…cdef
    set_by   TEXT,
    set_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_role (
    role     TEXT PRIMARY KEY,   -- drafter / questioner / extractor
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    set_by   TEXT,
    set_at   TEXT NOT NULL
);

-- Vendors beyond the presets: corporate intranet gateways, Azure deployments, local vLLM/Ollama.
-- Keys are not here - they share provider_key with the preset vendors, same encrypted path.
CREATE TABLE IF NOT EXISTS custom_provider (
    id            TEXT PRIMARY KEY,
    base_url      TEXT NOT NULL,
    default_model TEXT NOT NULL,
    added_by      TEXT,
    added_at      TEXT NOT NULL
);

-- Which models a vendor currently offers. **A fact about this machine's key, not content** -
-- so it goes into the user database, never the content pack.
CREATE TABLE IF NOT EXISTS model_catalog (
    provider    TEXT PRIMARY KEY,
    models_json TEXT NOT NULL DEFAULT '[]',
    fetched_at  TEXT NOT NULL,
    error       TEXT NOT NULL DEFAULT ''     -- empty = this fetch succeeded
);

CREATE TABLE IF NOT EXISTS llm_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    set_by TEXT,
    set_at TEXT NOT NULL
);

-- The drafting ledger. Append-only.
CREATE TABLE IF NOT EXISTS draft_spend (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    actor    TEXT,
    controls INTEGER NOT NULL,
    what     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_spend_at ON draft_spend(at);
"""

# Defaults decide what happens when nobody has configured anything - and that is the normal case.
# These three numbers must be **usable yet bounded**: they stop slips of the hand, not normal work.
DEFAULT_LIMITS = {
    "draft_cap_hour": 300,     # items per person per hour
    "draft_cap_month": 5000,   # items per organization per month
    "draft_max_jobs": 3,       # concurrent drafting jobs
}

HOUR = timedelta(hours=1)


class CustomProviderError(Exception):
    """A custom endpoint was misconfigured. This message is shown to the user verbatim."""


# A custom endpoint's key is stored in provider_key, but the registry looks keys up by
# environment variable name - so each custom endpoint gets a synthesized name. Setting that
# variable directly works for local deployments too - same rule as the presets' fallback.
def custom_key_env(provider_id: str) -> str:
    return f"FR_CUSTOM_{provider_id.upper().replace('-', '_')}_API_KEY"


_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

# http is allowed for exactly these. Ollama / vLLM / intranet gateways are that kind of
# deployment; a blanket https rule would lock out every self-hosted setup - and self-hosting
# is the main reason this opening exists.
_PRIVATE_HOSTNAMES = {"localhost"}


def check_base_url(url: str) -> None:
    """Address policy. Raises on violation - **before the database write**.

    Literal inspection only, no DNS resolution: where a domain resolves now and where it
    resolves when the request fires can be two different addresses. That is not a problem a
    config-time check can solve; it is documented here so nobody believes it was.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse((url or "").strip())
    except ValueError as exc:                       # too malformed even for urlparse
        raise CustomProviderError(f"not a valid URL: {url}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CustomProviderError(
            f"The address must start with http:// or https:// and include a hostname. You entered: {url}")
    if parsed.scheme == "https":
        return

    host = parsed.hostname
    if host in _PRIVATE_HOSTNAMES:
        return
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise CustomProviderError(
            f"http:// is only allowed for private addresses (loopback, 10.x, 172.16-31.x, 192.168.x)."
            f"{host} is a public domain - the key and framework text travel over this connection; use https."
        ) from None
    if not (addr.is_loopback or addr.is_private):
        raise CustomProviderError(
            f"http:// is only allowed for private addresses; {host} is a public IP. Use https.")


class BudgetError(Exception):
    """A gate was exceeded. The message is shown to the user, so it carries the numbers."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_path() -> Path:
    from framework_reader import usage

    # Same operations database as the identity layer: this is deployment config, not
    # business data - exporting "our compliance material" must not carry keys along.
    return usage.home() / "identity.sqlite"


class ModelConfig:
    _ready: set[Path] = set()

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        sqlite_setup.prepare(conn)
        if self.path not in ModelConfig._ready:
            conn.executescript(SCHEMA)
            conn.commit()
            ModelConfig._ready.add(self.path)
        return conn

    # ---------- key ----------

    def set_key(self, provider: str, key: str, *, by: str | None = None) -> None:
        """Seal first, then write. If sealing fails (no master key), **nothing is written**."""
        sealed = crypto.seal(key)          # on SecretError nothing after this line runs
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO provider_key (provider, sealed, masked, set_by, set_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET "
                "sealed = excluded.sealed, masked = excluded.masked, "
                "set_by = excluded.set_by, set_at = excluded.set_at",
                (provider, sealed, crypto.mask(key), by, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def key(self, provider: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT sealed FROM provider_key WHERE provider = ?",
                (provider,)).fetchone()
        finally:
            conn.close()
        return crypto.open_secret(row["sealed"]) if row else None

    def clear_key(self, provider: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM provider_key WHERE provider = ?", (provider,))
            # Key gone, that catalogue loses its meaning - keeping it suggests it is still selectable.
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider,))
            conn.commit()
        finally:
            conn.close()

    def masked(self) -> dict[str, dict]:
        """What the page gets contains **neither ciphertext nor plaintext** - only the recognizable last four characters."""
        conn = self._conn()
        try:
            return {
                r["provider"]: {"masked": r["masked"], "set_by": r["set_by"],
                                "set_at": r["set_at"]}
                for r in conn.execute(
                    "SELECT provider, masked, set_by, set_at FROM provider_key")
            }
        finally:
            conn.close()

    def key_lookup(self, env_lookup=None):
        """The key lookup handed to `LLMRegistry.build()`: store first, environment fallback second.

        The fallback exists for local deployments and first setup - while the store is still
        empty, the server's original environment variables keep working.
        """
        import os

        env_lookup = env_lookup or os.environ.get
        by_env = {}
        conn = self._conn()
        try:
            rows = list(conn.execute("SELECT provider, sealed FROM provider_key"))
        finally:
            conn.close()

        def lookup(env_name: str) -> str | None:
            from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

            if not by_env:
                registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
                sealed = {r["provider"]: r["sealed"] for r in rows}
                for preset in registry.providers:
                    if preset.id in sealed:
                        by_env[preset.api_key_env] = sealed[preset.id]
                # Custom endpoints are not in the YAML, but their keys live in the same table.
                # Without this branch, a key configured on the page still errors "no key" at draft time.
                for provider_id in self.custom_providers():
                    if provider_id in sealed:
                        by_env[custom_key_env(provider_id)] = sealed[provider_id]
            if env_name in by_env:
                return crypto.open_secret(by_env[env_name])
            return env_lookup(env_name)

        return lookup

    # ---------- Roles ----------

    def set_role(self, role: str, *, provider: str, model: str,
                 by: str | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO model_role (role, provider, model, set_by, set_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(role) DO UPDATE SET "
                "provider = excluded.provider, model = excluded.model, "
                "set_by = excluded.set_by, set_at = excluded.set_at",
                (role, provider, model, by, _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def roles(self) -> dict[str, dict]:
        """Unconfigured roles **do not appear** - fall back to content/llm_providers.yaml
        instead of inventing a default here. Two defaults in two places drift apart, eventually."""
        conn = self._conn()
        try:
            return {
                r["role"]: {"provider": r["provider"], "model": r["model"]}
                for r in conn.execute("SELECT role, provider, model FROM model_role")
            }
        finally:
            conn.close()

    # ---------- Model catalogue ----------

    def set_catalog(self, provider: str, models: list[str], *,
                    error: str = "") -> None:
        """Record successes and failures alike. The page must explain "why is there no dropdown here"."""
        import json

        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO model_catalog (provider, models_json, fetched_at, error) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET "
                "models_json = excluded.models_json, fetched_at = excluded.fetched_at, "
                "error = excluded.error",
                (provider, json.dumps(models, ensure_ascii=False),
                 _now().isoformat(), error),
            )
            conn.commit()
        finally:
            conn.close()

    def catalog(self, provider: str) -> dict | None:
        """Returns None when never fetched. **Never-fetched and fetched-but-empty are different
        things** - the page says different words, and merging them into one empty list loses the distinction."""
        import json

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT models_json, fetched_at, error FROM model_catalog "
                "WHERE provider = ?", (provider,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {"models": json.loads(row["models_json"]),
                "fetched_at": row["fetched_at"], "error": row["error"]}

    def catalogs(self) -> dict[str, dict]:
        import json

        conn = self._conn()
        try:
            return {
                r["provider"]: {"models": json.loads(r["models_json"]),
                                "fetched_at": r["fetched_at"], "error": r["error"]}
                for r in conn.execute(
                    "SELECT provider, models_json, fetched_at, error FROM model_catalog")
            }
        finally:
            conn.close()

    def clear_catalog(self, provider: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider,))
            conn.commit()
        finally:
            conn.close()

    # ---------- Custom endpoints ----------

    def set_custom_provider(self, provider_id: str, *, base_url: str,
                            default_model: str, by: str | None = None) -> None:
        """Validate first, then write. **Any single failure writes nothing.**"""
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        provider_id = (provider_id or "").strip()
        if not _ID_OK.match(provider_id):
            raise CustomProviderError(
                f"IDs may only use lowercase letters, digits, hyphens and underscores, and must start with a letter or digit: "
                f"{provider_id} is not allowed.")
        presets = {p.id for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers}
        if provider_id in presets:
            # On a name collision, whichever wins depends on lookup order - the most expensive kind of bug to find.
            raise CustomProviderError(f"{provider_id} is the id of a preset provider; pick another.")
        check_base_url(base_url)
        if not (default_model or "").strip():
            raise CustomProviderError("A default model name is required - custom endpoints have no default to guess.")

        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO custom_provider (id, base_url, default_model, added_by, added_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "base_url = excluded.base_url, default_model = excluded.default_model, "
                "added_by = excluded.added_by, added_at = excluded.added_at",
                (provider_id, base_url.strip(), default_model.strip(), by,
                 _now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def custom_providers(self) -> dict[str, dict]:
        conn = self._conn()
        try:
            return {
                r["id"]: {"base_url": r["base_url"], "default_model": r["default_model"],
                          "added_by": r["added_by"], "added_at": r["added_at"]}
                for r in conn.execute(
                    "SELECT id, base_url, default_model, added_by, added_at "
                    "FROM custom_provider ORDER BY id")
            }
        finally:
            conn.close()

    def delete_custom_provider(self, provider_id: str) -> None:
        """Refuse while any role still points here. Delete it and the drafter points at a nonexistent
        vendor - the next draft explodes, and by then nobody remembers this step did it.

        Same pattern as "revoking the last admin is refused": the invariant lives in the storage layer.
        """
        used_by = [role for role, cfg in self.roles().items()
                   if cfg["provider"] == provider_id]
        if used_by:
            raise CustomProviderError(
                f"{', '.join(sorted(used_by))} still points to {provider_id}."
                " Point those roles at another provider first, then delete.")
        conn = self._conn()
        try:
            conn.execute("DELETE FROM custom_provider WHERE id = ?", (provider_id,))
            conn.execute("DELETE FROM provider_key WHERE provider = ?", (provider_id,))
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider_id,))
            conn.commit()
        finally:
            conn.close()

    # ---------- Rate limits and budget ----------

    def limits(self) -> dict[str, int]:
        conn = self._conn()
        try:
            stored = {r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM llm_setting")}
        finally:
            conn.close()
        out = dict(DEFAULT_LIMITS)
        for name in DEFAULT_LIMITS:
            if name in stored and str(stored[name]).isdigit():
                out[name] = int(stored[name])
        return out

    def set_limits(self, *, by: str | None = None, **values: int) -> None:
        conn = self._conn()
        try:
            for name, value in values.items():
                if name not in DEFAULT_LIMITS:
                    raise ValueError(f"no such limit: {name}")
                if int(value) < 1:
                    # 0 would switch drafting off entirely - that job belongs to "revoke the author role".
                    raise ValueError("The limit must be at least 1. To stop drafting entirely, remove the author role.")
                conn.execute(
                    "INSERT INTO llm_setting (key, value, set_by, set_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, set_by = excluded.set_by, "
                    "set_at = excluded.set_at",
                    (name, str(int(value)), by, _now().isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    def charge_draft(self, actor: str, controls: int, *, what: str = "",
                     running_jobs: int = 0) -> None:
        """Check the three gates before booking. **A refusal books nothing** - charging on refusal makes the second attempt likelier to be refused too."""
        limits = self.limits()
        if running_jobs >= limits["draft_max_jobs"]:
            raise BudgetError(
                f"There are already {running_jobs} drafting jobs running (limit {limits['draft_max_jobs']})."
                " Wait for one to finish and try again.")
        if controls <= 0:
            return                      # nothing to draft; no charge due

        now = _now()
        conn = self._conn()
        try:
            mine = conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend "
                "WHERE actor = ? AND at >= ?",
                (actor, (now - HOUR).isoformat()),
            ).fetchone()[0]
            if mine + controls > limits["draft_cap_hour"]:
                raise BudgetError(
                    f"In this hour you have already drafted {mine} controls; another {controls} would exceed the "
                    f"per-person hourly cap of {limits['draft_cap_hour']} controls."
                    " Wait a while, or have an admin raise the cap on the Models page.")
            month = conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend WHERE at >= ?",
                (now.replace(day=1, hour=0, minute=0, second=0,
                             microsecond=0).isoformat(),),
            ).fetchone()[0]
            if month + controls > limits["draft_cap_month"]:
                raise BudgetError(
                    f"This month the whole organization has already drafted {month} controls; another {controls} would exceed the "
                    f"monthly budget of {limits['draft_cap_month']} controls."
                    " Have an admin adjust the budget on the Models page.")
            conn.execute(
                "INSERT INTO draft_spend (at, actor, controls, what) "
                "VALUES (?, ?, ?, ?)",
                (now.isoformat(), actor, int(controls), what),
            )
            conn.commit()
        finally:
            conn.close()

    def remaining_draft(self, actor: str) -> int:
        """How many drafts remain this hour and this month. The tighter one wins."""
        limits = self.limits()
        now = _now()
        conn = self._conn()
        try:
            mine = conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend "
                "WHERE actor = ? AND at >= ?",
                (actor, (now - HOUR).isoformat()),
            ).fetchone()[0]
            month = conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend WHERE at >= ?",
                (now.replace(day=1, hour=0, minute=0, second=0,
                             microsecond=0).isoformat(),),
            ).fetchone()[0]
        finally:
            conn.close()
        hour_left = limits["draft_cap_hour"] - int(mine)
        month_left = limits["draft_cap_month"] - int(month)
        return max(0, min(hour_left, month_left))

    def refund_draft(self, actor: str, controls: int) -> None:
        """Refund a charge whose job never ran.

        charge_draft's rule is "a refusal books nothing" - that refusal happens **at the gate**.
        Pass the gate, book the charge, then fail later (say, no key configured): keeping the
        charge is a pure loss - the next attempt is likelier to be refused, and the user got nothing.
        """
        if controls <= 0:
            return
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO draft_spend (at, actor, controls, what) "
                "VALUES (?, ?, ?, ?)",
                (_now().isoformat(), actor, -int(controls), "refund: run failed"))
            conn.commit()
        finally:
            conn.close()

    def spent_this_month(self) -> int:
        conn = self._conn()
        try:
            start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend WHERE at >= ?",
                (start.isoformat(),)).fetchone()[0]
        finally:
            conn.close()

    def spent_this_hour(self, actor: str) -> int:
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT COALESCE(SUM(controls), 0) FROM draft_spend "
                "WHERE actor = ? AND at >= ?",
                (actor, (_now() - HOUR).isoformat())).fetchone()[0]
        finally:
            conn.close()


def effective_registry(path=None, config: "ModelConfig | None" = None):
    """Overlay what the admin configured on the web onto the YAML presets; return (registry, key_lookup).

    **Assembled in exactly one place.** CLI and web share one drafting path (interpret/run.py),
    so a change here reaches both - write it twice and the two copies drift apart.

    Roles not configured on the web keep their YAML presets; keys not configured fall back to
    environment variables. Two defaults in two places drift apart, eventually.
    """
    from framework_reader.llm.registry import (
        DEFAULT_REGISTRY_PATH, LLMRegistry, RoleConfig,
    )

    from framework_reader.llm.registry import ProviderPreset

    config = config or ModelConfig()
    registry = LLMRegistry.load(path or DEFAULT_REGISTRY_PATH)
    # Synthesize custom endpoints as presets into the same catalogue - registry.build and
    # GuardedClient stay untouched; outbound traffic still has exactly one path.
    for provider_id, row in config.custom_providers().items():
        registry.providers.append(ProviderPreset(
            id=provider_id,
            kind="openai_compat",          # custom endpoints always use the compat interface
            base_url=row["base_url"],
            api_key_env=custom_key_env(provider_id),
            default_model=row["default_model"],
            explicit_cache=False,          # only anthropic can claim explicit caching
            note="custom endpoint",
        ))
    for role, chosen in config.roles().items():
        registry.roles[role] = RoleConfig(**chosen)
    return registry, config.key_lookup()
