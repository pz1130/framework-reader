"""管理员配的模型、key、限速与预算。见网页服务化设计 §6⑤⑥

本地部署时这些都在环境变量里，读一次就完了。联网之后有两件事变了：

- **key 存在我们的服务器上。** 所以加密落库、只脱敏回显、永不出现在
  日志与异常里。没有主密钥（`FR_SECRET_KEY`）就拒绝落库——悄悄明文存下来
  是这里唯一不可接受的失败方式。
- **起草花的是组织的钱。** 一个没有上限的花钱按钮，第一个手滑的人就能
  把一个月的预算点完。所以每人每小时、全组织每月、同时几个任务，三道闸。

**账在「条」上，不在「元」上。** 我们没有各家厂商的实时价目，硬折成钱只会
给人一个精确的错觉。「这个月起草了多少条」是能数准的，也是唯一诚实的度量。

**在开跑那一刻记账，不是跑完。** 跑完再记的话，跑到一半的任务不算数，
而钱已经花掉了。宁可多算失败的那几条。
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
    sealed   TEXT NOT NULL,      -- 密文。主密钥在 FR_SECRET_KEY，不在这张表里
    masked   TEXT NOT NULL,      -- 回显用，sk-…cdef
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

-- 预设里没有的厂商：公司内网网关、Azure 部署、本机 vLLM/Ollama。
-- key 不在这里——它和预设厂商共用 provider_key，同一条加密路径。
CREATE TABLE IF NOT EXISTS custom_provider (
    id            TEXT PRIMARY KEY,
    base_url      TEXT NOT NULL,
    default_model TEXT NOT NULL,
    added_by      TEXT,
    added_at      TEXT NOT NULL
);

-- 某厂商此刻有哪些模型可用。**这是这台机器上这把 key 的事实，不是内容**，
-- 所以进用户库，永不进内容包。
CREATE TABLE IF NOT EXISTS model_catalog (
    provider    TEXT PRIMARY KEY,
    models_json TEXT NOT NULL DEFAULT '[]',
    fetched_at  TEXT NOT NULL,
    error       TEXT NOT NULL DEFAULT ''     -- 空 = 这次拉成功了
);

CREATE TABLE IF NOT EXISTS llm_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    set_by TEXT,
    set_at TEXT NOT NULL
);

-- 起草的账。只追加。
CREATE TABLE IF NOT EXISTS draft_spend (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    actor    TEXT,
    controls INTEGER NOT NULL,
    what     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_spend_at ON draft_spend(at);
"""

# 默认值决定了没人配置时会发生什么，而没人配置是常态。
# 这三个数要**够用而有限**：挡得住手滑，挡不住正常工作。
DEFAULT_LIMITS = {
    "draft_cap_hour": 300,     # 每人每小时多少条
    "draft_cap_month": 5000,   # 全组织每月多少条
    "draft_max_jobs": 3,       # 同时几个起草任务
}

HOUR = timedelta(hours=1)


class CustomProviderError(Exception):
    """自定义端点配错了。这句话直接给用户看。"""


# 自定义端点的 key 存在 provider_key 里，但 registry 是按环境变量名取 key 的，
# 所以给每个自定义端点合成一个名字。本地部署直接设这个环境变量也能跑——
# 和预设厂商回落环境变量是同一套规矩。
def custom_key_env(provider_id: str) -> str:
    return f"FR_CUSTOM_{provider_id.upper().replace('-', '_')}_API_KEY"


_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

# http 只放行这些。Ollama / vLLM / 内网网关正是这一类，一刀切成 https
# 会把自建部署全堵死——而自建部署恰恰是开这个口子的主要理由。
_PRIVATE_HOSTNAMES = {"localhost"}


def check_base_url(url: str) -> None:
    """地址政策。不合规就抛，**在写库之前**。

    只看字面量，不做 DNS 解析：一个域名此刻解析到哪、请求发出时解析到哪，
    可以是两个地址。那不是一道配置期校验能解决的问题，写在这里是免得
    有人以为它解决了。
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse((url or "").strip())
    except ValueError as exc:                       # 畸形到 urlparse 都不收
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
    """超了闸。这句话直接给用户看，所以要带上数字。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_path() -> Path:
    from framework_reader import usage

    # 与身份层同一个运营库：这是部署配置，不是业务数据，
    # 导出「我们的合规材料」时不该顺带把 key 带出去。
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
        """先封再写。封不上（没有主密钥）就**什么都不写**。"""
        sealed = crypto.seal(key)          # 抛 SecretError 时这一行之后都不会跑
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
            # key 没了，那份清单也失去意义——留着只会让人以为还能选。
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider,))
            conn.commit()
        finally:
            conn.close()

    def masked(self) -> dict[str, dict]:
        """给页面的东西里**没有密文，也没有明文**——只有认得出的那几位。"""
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
        """给 `LLMRegistry.build()` 的取 key 函数：先看库，再回落环境变量。

        回落是留给本地部署与首次搭建的——库里还什么都没有的时候，
        服务器上原来那套环境变量照样能跑。
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
                # 自定义端点不在 YAML 里，但 key 存在同一张表。漏掉这一段的话
                # 页面上配得好好的 key，起草时报「没配 key」。
                for provider_id in self.custom_providers():
                    if provider_id in sealed:
                        by_env[custom_key_env(provider_id)] = sealed[provider_id]
            if env_name in by_env:
                return crypto.open_secret(by_env[env_name])
            return env_lookup(env_name)

        return lookup

    # ---------- 角色 ----------

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
        """没配的角色**不出现**——回落到 content/llm_providers.yaml，
        不在这里编一个默认值出来。两处各有一个默认值，迟早对不上。"""
        conn = self._conn()
        try:
            return {
                r["role"]: {"provider": r["provider"], "model": r["model"]}
                for r in conn.execute("SELECT role, provider, model FROM model_role")
            }
        finally:
            conn.close()

    # ---------- 模型目录 ----------

    def set_catalog(self, provider: str, models: list[str], *,
                    error: str = "") -> None:
        """成功与失败都记。页面要说清楚「为什么这儿没有下拉」。"""
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
        """没拉过返回 None。**「没拉过」与「拉到了但是空的」是两件事**——
        页面上说的话不一样，混成一个空列表就分不出来了。"""
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

    # ---------- 自定义端点 ----------

    def set_custom_provider(self, provider_id: str, *, base_url: str,
                            default_model: str, by: str | None = None) -> None:
        """先校验，再写。**任何一条不过就什么都不写。**"""
        from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

        provider_id = (provider_id or "").strip()
        if not _ID_OK.match(provider_id):
            raise CustomProviderError(
                f"IDs may only use lowercase letters, digits, hyphens and underscores, and must start with a letter or digit: "
                f"{provider_id} is not allowed.")
        presets = {p.id for p in LLMRegistry.load(DEFAULT_REGISTRY_PATH).providers}
        if provider_id in presets:
            # 撞名的话，谁盖谁全看查表顺序——那种 bug 找起来最贵。
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
        """还有角色指着它就拒绝。删掉之后 drafter 指向一个不存在的厂商，
        下一次起草才会炸——那时候没人记得是这一步干的。

        跟「撤最后一个 admin 会被拒绝」同一个模式：不变量守在存储层。
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

    # ---------- 限速与预算 ----------

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
                    # 0 会把起草整个关死，而那件事该由「撤掉 author 角色」来做。
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
        """先查三道闸，过了才记账。**拒了不记**——拒了还扣，等于第二次更容易被拒。"""
        limits = self.limits()
        if running_jobs >= limits["draft_max_jobs"]:
            raise BudgetError(
                f"There are already {running_jobs} drafting jobs running (limit {limits['draft_max_jobs']})."
                " Wait for one to finish and try again.")
        if controls <= 0:
            return                      # 一条都不用起草，不该扣额度

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
        """这一小时、这个月还能起草几条。取更紧的那个。"""
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
        """扣过额度但活儿没跑成，退回去。

        `charge_draft` 的规矩是「拒了不记」，那是在**查闸的时候**拒。
        闸过了、钱记了、然后在更后面一步失败（比如没配 key），
        那笔账留着就是白扣——下一次更容易被拒，而用户什么都没得到。
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
    """把管理员在网页上配的东西盖到 YAML 预设上，返回 (registry, key_lookup)。

    **只此一处组装。** CLI 与 Web 走同一条起草路径（`interpret/run.py`），
    所以这里改一次，两边都跟着变——分成两处写，两处就会各自漂。

    没在网页上配的角色保持 YAML 预设，没在网页上配的 key 回落环境变量。
    两处各编一个默认值，迟早对不上。
    """
    from framework_reader.llm.registry import (
        DEFAULT_REGISTRY_PATH, LLMRegistry, RoleConfig,
    )

    from framework_reader.llm.registry import ProviderPreset

    config = config or ModelConfig()
    registry = LLMRegistry.load(path or DEFAULT_REGISTRY_PATH)
    # 自定义端点合成成 preset 塞进同一份清单——registry.build 与 GuardedClient
    # 一个字不动，出网仍然只有那一条路径。
    for provider_id, row in config.custom_providers().items():
        registry.providers.append(ProviderPreset(
            id=provider_id,
            kind="openai_compat",          # 自定义端点一律走兼容口
            base_url=row["base_url"],
            api_key_env=custom_key_env(provider_id),
            default_model=row["default_model"],
            explicit_cache=False,          # 显式缓存只有 anthropic 能声称
            note="custom endpoint",
        ))
    for role, chosen in config.roles().items():
        registry.roles[role] = RoleConfig(**chosen)
    return registry, config.key_lookup()
