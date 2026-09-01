# 模型目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理员在 `/models` 页保存某厂商的 API key 之后，自动去该厂商拉一次可用模型清单，缓存进库，模型名那一栏从手填文本框变成「下拉 + 手填」。

**Architecture:** 新增 `llm/catalog.py`，与两个 chat 适配器平级：一个纯函数 `fetch_models(...)` 负责发请求与解析，真实出网收在 `_default_get` 一个函数里、可注入替换。结果落 `ModelConfig` 同一个库的新表 `model_catalog`。`/models/key` 这条既有路由在保存 key 之后调用它，失败一律不阻断保存。

**Tech Stack:** Python 3.12、FastAPI、SQLite、httpx（只在 `_default_get` 内部按需 import）、pytest。

**Spec:** `docs/superpowers/specs/2026-08-24-model-catalog-design.md`

## Global Constraints

- **携带内容的出网只有一条路径**：`llm/registry.py` 组装并被 `GuardedClient` 包住。模型目录不携带任何内容，属于第二类（身份 OIDC 同类），但必须收在一个可注入替换的 `_default_*` 函数里。见 spec §0。
- **测试永不真实出网**：`tests/test_no_network_in_tests.py` 断言没有测试触碰 `_default_post` / `_default_send` / `_default_fetch`，本计划再加上 `_default_get`。测试一律注入假 `http_get`。
- **测试不读进程环境**：不许出现 `os.environ` / `os.getenv`（同一份测试在管）。
- **API key 一个字符都不进日志、不进异常消息、不进页面**。页面只回显 `crypto.mask()` 的结果。
- **手填框永远保留**：下拉是便利，不是唯一入口。spec §3。
- **超时 15 秒**（chat 是 120 秒）。spec §1。
- 所有新代码注释与用户可见文案用中文，与仓库既有风格一致。
- 每个任务结束时 `.venv/bin/python -m pytest -q` 全绿才提交。

## File Structure

| 文件 | 职责 |
|---|---|
| `src/framework_reader/llm/catalog.py` | **新建。** 发目录请求、解析、过滤。唯一真实出网点 `_default_get`。不碰数据库。 |
| `src/framework_reader/llm/config.py` | **改。** 加 `model_catalog` 表与三个方法：`set_catalog` / `catalog` / `catalogs`。`clear_key` 顺带清目录。 |
| `src/framework_reader/web/app.py` | **改。** `/models/key` 保存后触发拉取；新增 `/models/catalog/refresh` 路由。 |
| `src/framework_reader/web/views.py` | **改。** 模型名一栏改成「下拉 + 手填」；厂商一览加「你的 key」列。 |
| `tests/llm/test_catalog.py` | **新建。** 解析、过滤、三条失败分支。 |
| `tests/llm/test_catalog_store.py` | **新建。** 存取、清 key 连带清目录。 |
| `tests/web/test_model_catalog.py` | **新建。** 端到端：保存 key → 拉取 → 页面上出现下拉；刷新；手填仍可用。 |
| `tests/test_no_network_in_tests.py` | **改。** 禁用清单加 `_default_get`；新增 httpx 白名单断言。 |

---

### Task 1: 目录抓取与解析（不碰数据库、不碰网页）

**Files:**
- Create: `src/framework_reader/llm/catalog.py`
- Test: `tests/llm/test_catalog.py`

**Interfaces:**
- Consumes: `framework_reader.llm.registry.ProviderPreset`（字段 `id` / `kind` / `base_url` / `default_model` / `verified` / `note`）
- Produces:
  - `CatalogError(Exception)`，带属性 `kind: str`，取值 `"auth"` / `"unsupported"` / `"unreachable"`
  - `HttpGet = Callable[[str, dict], dict]`（url, headers → 解析好的 JSON dict）
  - `fetch_models(preset: ProviderPreset, api_key: str, *, http_get: HttpGet | None = None) -> list[str]`
  - `NON_CHAT_MARKERS: tuple[str, ...]`
  - `_default_get(url: str, headers: dict) -> dict`

- [ ] **Step 1: 写失败的测试**

创建 `tests/llm/test_catalog.py`：

```python
"""厂商模型目录。见 docs/superpowers/specs/2026-08-24-model-catalog-design.md

这是第二类出网：不携带任何内容（没有控制条款、没有制度正文），只问
「你这儿有哪些模型」。真实请求收在 `_default_get` 一个函数里，测试一律注入假的。
"""
import pytest

from framework_reader.llm.catalog import CatalogError, fetch_models
from framework_reader.llm.registry import ProviderPreset

DEEPSEEK = ProviderPreset(
    id="deepseek", kind="openai_compat", base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY", default_model="deepseek-chat")
CLAUDE = ProviderPreset(
    id="anthropic", kind="anthropic", base_url="",
    api_key_env="ANTHROPIC_API_KEY", default_model="claude-opus-5")


def _fake(payload, *, seen=None):
    def get(url, headers):
        if seen is not None:
            seen.append((url, headers))
        return payload
    return get


def test_openai_format_is_parsed():
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}))
    assert got == ["deepseek-chat", "deepseek-reasoner"]


def test_it_asks_the_right_url_with_a_bearer_token():
    seen = []
    fetch_models(DEEPSEEK, "sk-x", http_get=_fake({"data": []}, seen=seen))
    url, headers = seen[0]
    assert url == "https://api.deepseek.com/models"
    assert headers["Authorization"] == "Bearer sk-x"


def test_anthropic_uses_its_own_url_and_headers():
    """anthropic 的 base_url 是空的，且它要 x-api-key 与 anthropic-version。"""
    seen = []
    fetch_models(CLAUDE, "sk-ant-x", http_get=_fake({"data": []}, seen=seen))
    url, headers = seen[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "sk-ant-x"
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


def test_results_are_sorted_and_deduped():
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}))
    assert got == ["a", "b"]


def test_entries_without_an_id_are_skipped_not_fatal():
    """厂商多返回一个字段是常事，少一个 id 不该让整次拉取失败。"""
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": "a"}, {"object": "model"}, {"id": ""}]}))
    assert got == ["a"]


def test_an_empty_catalog_is_not_an_error():
    assert fetch_models(DEEPSEEK, "sk-x", http_get=_fake({"data": []})) == []


@pytest.mark.parametrize("payload", [{}, {"data": "不是列表"}, {"models": []}])
def test_a_shape_we_do_not_understand_counts_as_unsupported(payload):
    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=_fake(payload))
    assert exc.value.kind == "unsupported"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"),
                                         (404, "unsupported"), (500, "unreachable")])
def test_http_errors_map_to_three_kinds(status, kind):
    def boom(url, headers):
        raise _HttpStatus(status)

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=boom)
    assert exc.value.kind == kind


def test_any_other_exception_is_unreachable():
    def boom(url, headers):
        raise TimeoutError("超时")

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-x", http_get=boom)
    assert exc.value.kind == "unreachable"


def test_the_key_never_appears_in_the_error_message():
    """异常消息会被原样渲到页面上。"""
    def boom(url, headers):
        raise _HttpStatus(401)

    with pytest.raises(CatalogError) as exc:
        fetch_models(DEEPSEEK, "sk-live-0123456789abcdef", http_get=boom)
    assert "sk-live-0123456789abcdef" not in str(exc.value)
    assert "0123456789abcdef" not in str(exc.value)


# ---------- 过滤 ----------

@pytest.mark.parametrize("model_id", [
    "text-embedding-3-large", "BAAI/bge-reranker-v2-m3", "tts-1",
    "whisper-large-v3", "omni-moderation-latest", "black-forest-labs/FLUX.1",
    "stable-diffusion-3", "gpt-image-1",
])
def test_non_chat_models_are_filtered_out(model_id):
    got = fetch_models(DEEPSEEK, "sk-x", http_get=_fake(
        {"data": [{"id": model_id}, {"id": "deepseek-chat"}]}))
    assert got == ["deepseek-chat"]


@pytest.mark.parametrize("model_id", [
    "deepseek-chat", "deepseek-reasoner", "qwen-max", "glm-4-plus",
    "kimi-latest", "claude-opus-5", "gpt-4o", "llama-3.3-70b-versatile",
    "deepseek-ai/DeepSeek-V3", "mistral-large-latest", "step-2-16k",
])
def test_real_chat_models_survive_the_filter(model_id):
    """过滤清单会误伤——用真实模型名钉住它至少不误伤这些。"""
    assert fetch_models(DEEPSEEK, "sk-x",
                        http_get=_fake({"data": [{"id": model_id}]})) == [model_id]


class _HttpStatus(Exception):
    """假的 HTTP 状态异常，形状与 httpx.HTTPStatusError 对齐（有 .response.status_code）。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/llm/test_catalog.py -q`
Expected: 收集就失败——`ModuleNotFoundError: No module named 'framework_reader.llm.catalog'`

- [ ] **Step 3: 写最小实现**

创建 `src/framework_reader/llm/catalog.py`：

```python
"""厂商模型目录：问「你这儿有哪些模型」。见 2026-08-24 模型目录设计

**这是第二类出网。** 携带内容（控制条款、解读、配套文档节选）的出网只有
`llm/registry.py` 组装、被 `GuardedClient` 包住那一条；这里一个字的内容都不带，
只发一个 GET 和一把 key。同类的还有 `identity/entra.py` 的 OIDC。
规矩是一样的：真实请求收在 `_default_get` 一个函数里，可注入替换，
且有测试断言没有任何测试碰它。

不碰数据库。缓存是 `llm/config.py` 的事——这里只负责「问到了什么」。
"""
from collections.abc import Callable

from framework_reader.llm.registry import ProviderPreset

HttpGet = Callable[[str, dict], dict]

# 目录查询等 15 秒还不回，就当它不支持。chat 那边是 120 秒，
# 那是给模型生成留的时间，跟列目录不是一回事。
TIMEOUT_SECONDS = 15.0

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# 按 id 子串剔除。siliconflow 与 openrouter 的目录里有一两百条这类模型，
# 它们永远不会被用作 drafter，留在下拉里只会让人翻不到想要的那条。
#
# **这份清单会误伤**：某天某家把对话模型起名带 `vision-ocr`，它就被吃掉了。
# 代价可接受，因为手填框永远保留——误伤的后果是「下拉里没有，手填一下」，
# 不是「用不了」。
NON_CHAT_MARKERS = (
    "embed", "rerank", "tts", "whisper", "audio", "moderation",
    "image", "vision-ocr", "stable-diffusion", "flux",
)


class CatalogError(Exception):
    """拉目录失败。`kind` 决定页面上说什么话。

    - `auth`        —— 这把 key 被拒了（401/403）
    - `unsupported` —— 这家不提供目录，或返回的形状我们不认识（404/解析不出）
    - `unreachable` —— 超时、连不上、5xx
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _default_get(url: str, headers: dict) -> dict:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _request(preset: ProviderPreset, api_key: str) -> tuple[str, dict]:
    if preset.kind == "anthropic":
        return ANTHROPIC_MODELS_URL, {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return f"{preset.base_url.rstrip('/')}/models", {
        "Authorization": f"Bearer {api_key}",
    }


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in NON_CHAT_MARKERS)


def fetch_models(
    preset: ProviderPreset, api_key: str, *, http_get: HttpGet | None = None
) -> list[str]:
    """问一次目录，回一份排好序、去过重、滤掉非对话模型的 id 列表。

    **异常消息里绝不出现 key。** 这个消息会被原样渲到页面上。
    """
    get = http_get or _default_get
    url, headers = _request(preset, api_key)
    try:
        payload = get(url, headers)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要翻译成三种之一
        status = _status_of(exc)
        if status in (401, 403):
            raise CatalogError(
                "auth",
                f"{preset.id} 拒绝了这把 key。如果你确定它没错，"
                "可能是这家的目录接口需要额外权限——key 已经保存，起草照样能跑。",
            ) from None
        if status == 404:
            raise CatalogError(
                "unsupported", f"{preset.id} 不提供模型目录，模型名手填。") from None
        raise CatalogError(
            "unreachable", f"没能连上 {preset.id}，可以稍后点刷新。") from None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise CatalogError(
            "unsupported",
            f"{preset.id} 返回的目录格式不认识，模型名手填。")

    ids = {
        str(row["id"]).strip()
        for row in data
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    return sorted(m for m in ids if is_chat_model(m))
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `.venv/bin/python -m pytest tests/llm/test_catalog.py -q`
Expected: PASS（约 30 条）

- [ ] **Step 5: 提交**

```bash
git add src/framework_reader/llm/catalog.py tests/llm/test_catalog.py
git commit -m "feat(llm): 模型目录抓取与解析

第二类出网：不携带任何内容，只问「你这儿有哪些模型」。真实请求收在
_default_get 一个函数里，可注入替换。失败翻译成三种：auth / unsupported /
unreachable，各自决定页面上说什么话；异常消息里绝不出现 key。

过滤掉 embedding/rerank/tts 等非对话模型——这份清单会误伤，代价可接受，
因为手填框永远保留。一组真实模型名钉住它至少不误伤那些。"
```

---

### Task 2: 目录落库

**Files:**
- Modify: `src/framework_reader/llm/config.py`（`SCHEMA` 常量；`clear_key`；新增三个方法）
- Test: `tests/llm/test_catalog_store.py`

**Interfaces:**
- Consumes: Task 1 的 `CatalogError`
- Produces（都是 `ModelConfig` 的方法）：
  - `set_catalog(provider: str, models: list[str], *, error: str = "") -> None`
  - `catalog(provider: str) -> dict | None` —— `{"models": list[str], "fetched_at": str, "error": str}`，没拉过返回 `None`
  - `catalogs() -> dict[str, dict]` —— provider → 上面那个 dict

- [ ] **Step 1: 写失败的测试**

创建 `tests/llm/test_catalog_store.py`：

```python
"""目录缓存。页面从缓存读，不在渲染时出网。"""
import pytest

from framework_reader import crypto
from framework_reader.llm.config import ModelConfig


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    return ModelConfig()


def test_nothing_fetched_yet_is_none_not_an_empty_list(config):
    """「没拉过」和「拉到了但是空的」是两件事，页面上说的话也不一样。"""
    assert config.catalog("deepseek") is None


def test_a_catalog_comes_back_with_when_it_was_fetched(config):
    config.set_catalog("deepseek", ["deepseek-chat", "deepseek-reasoner"])
    got = config.catalog("deepseek")
    assert got["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert got["error"] == ""
    assert got["fetched_at"]        # 一份不知道多旧的清单和没有清单一样危险


def test_a_failure_is_stored_too(config):
    """失败也要记：页面要说清楚「为什么这儿没有下拉」。"""
    config.set_catalog("qwen", [], error="qwen 不提供模型目录，模型名手填。")
    got = config.catalog("qwen")
    assert got["models"] == []
    assert "不提供模型目录" in got["error"]


def test_fetching_again_replaces_the_old_one(config):
    config.set_catalog("deepseek", ["old"])
    config.set_catalog("deepseek", ["new"])
    assert config.catalog("deepseek")["models"] == ["new"]


def test_a_successful_fetch_clears_a_previous_error(config):
    config.set_catalog("deepseek", [], error="没能连上")
    config.set_catalog("deepseek", ["deepseek-chat"])
    assert config.catalog("deepseek")["error"] == ""


def test_catalogs_lists_every_provider(config):
    config.set_catalog("deepseek", ["a"])
    config.set_catalog("qwen", [], error="x")
    assert set(config.catalogs()) == {"deepseek", "qwen"}


def test_clearing_the_key_clears_the_catalog(config):
    """key 没了，那份清单也失去意义——留着只会让人以为还能选。"""
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    config.set_catalog("deepseek", ["deepseek-chat"])
    config.clear_key("deepseek")
    assert config.catalog("deepseek") is None


def test_deleting_a_custom_provider_clears_its_catalog(config):
    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="m", by="boss@acme.cn")
    config.set_catalog("corp-gw", ["m"])
    config.delete_custom_provider("corp-gw")
    assert config.catalog("corp-gw") is None
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/llm/test_catalog_store.py -q`
Expected: FAIL —— `AttributeError: 'ModelConfig' object has no attribute 'catalog'`

- [ ] **Step 3: 写实现**

在 `src/framework_reader/llm/config.py` 的 `SCHEMA` 字符串里，`CREATE TABLE IF NOT EXISTS llm_setting (` 这一行**之前**插入：

```sql
-- 某厂商此刻有哪些模型可用。**这是这台机器上这把 key 的事实，不是内容**，
-- 所以进用户库，永不进内容包。
CREATE TABLE IF NOT EXISTS model_catalog (
    provider    TEXT PRIMARY KEY,
    models_json TEXT NOT NULL DEFAULT '[]',
    fetched_at  TEXT NOT NULL,
    error       TEXT NOT NULL DEFAULT ''     -- 空 = 这次拉成功了
);
```

在 `# ---------- 自定义端点 ----------` 这一行**之前**插入三个方法：

```python
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
```

在 `clear_key` 里，`conn.execute("DELETE FROM provider_key WHERE provider = ?", (provider,))` 之后加一行：

```python
            # key 没了，那份清单也失去意义——留着只会让人以为还能选。
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider,))
```

在 `delete_custom_provider` 里，`conn.execute("DELETE FROM provider_key WHERE provider = ?", (provider_id,))` 之后加一行：

```python
            conn.execute("DELETE FROM model_catalog WHERE provider = ?", (provider_id,))
```

- [ ] **Step 4: 跑测试确认它绿**

Run: `.venv/bin/python -m pytest tests/llm/ -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/framework_reader/llm/config.py tests/llm/test_catalog_store.py
git commit -m "feat(llm): 目录缓存落库

页面从缓存读，渲染时不出网。成功与失败都记——页面要说清楚「为什么这儿没有下拉」。
「没拉过」返回 None 而不是空列表：它和「拉到了但是空的」在页面上说的话不一样。

清 key 与删自定义端点时一并清掉目录：key 没了，那份清单也失去意义。"
```

---

### Task 3: 保存 key 之后自动拉一次 + 刷新路由

**Files:**
- Modify: `src/framework_reader/web/app.py`（`models_key` 路由；新增 `models_catalog_refresh` 路由）
- Test: `tests/web/test_model_catalog.py`

**Interfaces:**
- Consumes: Task 1 的 `fetch_models` / `CatalogError`；Task 2 的 `set_catalog` / `catalog`
- Produces:
  - `create_app(db, *, http_get=None)` —— 新增一个仅供测试注入的关键字参数，默认 `None` 表示用 `catalog._default_get`
  - 路由 `POST /models/catalog/refresh`，表单字段 `provider`，权限 `MODEL_WRITE`

- [ ] **Step 1: 写失败的测试**

创建 `tests/web/test_model_catalog.py`：

```python
"""配完 key 自动拉一次目录。见 2026-08-24 模型目录设计

**拉不到不影响存 key**：有些厂商的 /models 要额外权限，而同一把 key 跑 chat
完全没问题。拿目录接口的拒绝去否决一把能用的 key，是把人卡死在我们自己造的关卡上。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader import crypto
from framework_reader.identity.store import IdentityStore
from framework_reader.llm.catalog import CatalogError
from framework_reader.llm.config import ModelConfig
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

KEY = "sk-live-0123456789abcdef"


def _make(tmp_path, monkeypatch, http_get):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()

    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    app = create_app(db, http_get=http_get)
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"email": "boss@acme.cn", "password": "pw-boss-boss"})
    return client, ModelConfig()


def _post(client, path, **data):
    page = client.get("/").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return client.post(path, data={"csrf": found.group(1) if found else "", **data})


@pytest.fixture
def ok(tmp_path, monkeypatch):
    calls = []

    def http_get(url, headers):
        calls.append(url)
        return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    client, config = _make(tmp_path, monkeypatch, http_get)
    return client, config, calls


def test_saving_a_key_fetches_the_catalog(ok):
    client, config, calls = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert config.catalog("deepseek")["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert len(calls) == 1


def test_the_models_show_up_as_a_dropdown(ok):
    client, _, _ = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = client.get("/models").text
    assert "deepseek-reasoner" in page


def test_the_page_says_when_it_was_fetched(ok):
    """一份不知道多旧的清单，和没有清单一样危险。"""
    client, _, _ = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert "拉取" in client.get("/models").text


def test_opening_the_page_does_not_go_out(ok):
    """自动拉取只发生在保存 key 那一刻。页面加载不触发出网。"""
    client, _, calls = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    before = len(calls)
    client.get("/models")
    client.get("/models")
    assert len(calls) == before


def test_refresh_goes_out_again(ok):
    client, _, calls = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    before = len(calls)
    assert _post(client, "/models/catalog/refresh",
                 provider="deepseek").status_code == 303
    assert len(calls) == before + 1


# ---------- 失败一律不阻断 ----------

def _failing(kind):
    def http_get(url, headers):
        raise CatalogError(kind, {"auth": "被拒了", "unsupported": "不提供模型目录",
                                  "unreachable": "没能连上"}[kind])
    return http_get


@pytest.mark.parametrize("kind", ["auth", "unsupported", "unreachable"])
def test_the_key_is_saved_even_when_the_fetch_fails(tmp_path, monkeypatch, kind):
    client, config = _make(tmp_path, monkeypatch, _failing(kind))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert config.key("deepseek") == KEY


@pytest.mark.parametrize("kind,says", [
    ("auth", "被拒"), ("unsupported", "不提供模型目录"), ("unreachable", "没能连上")])
def test_the_page_explains_why_there_is_no_dropdown(tmp_path, monkeypatch, kind, says):
    client, _ = _make(tmp_path, monkeypatch, _failing(kind))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert says in client.get("/models").text


def test_a_failed_fetch_never_shows_the_key(tmp_path, monkeypatch):
    client, _ = _make(tmp_path, monkeypatch, _failing("auth"))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = client.get("/models").text
    assert KEY not in page and "0123456789abcdef" not in page


def test_typing_a_model_by_hand_still_works(tmp_path, monkeypatch):
    """下拉是便利，不是唯一入口。新模型上线永远早于任何目录。"""
    client, config = _make(tmp_path, monkeypatch, _failing("unsupported"))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert _post(client, "/models/role", role="drafter",
                 provider="deepseek", model="deepseek-v9-brand-new").status_code == 303
    assert config.roles()["drafter"]["model"] == "deepseek-v9-brand-new"
```

- [ ] **Step 2: 跑一遍确认它红**

Run: `.venv/bin/python -m pytest tests/web/test_model_catalog.py -q`
Expected: FAIL —— `create_app() got an unexpected keyword argument 'http_get'`

- [ ] **Step 3: 写实现**

在 `src/framework_reader/web/app.py` 里：

1. `create_app` 的签名加一个关键字参数。当前签名（`src/framework_reader/web/app.py:33`）是：

```python
def create_app(
    db: Path = DEFAULT_DB, draft_runner=None, rewrite_runner=None,
    user_db: Path | None = None, identity_db: Path | None = None,
    secure_cookies: bool = False, entra=None, entra_fetch=None,
```

在 `entra_fetch=None,` 之后加 `http_get=None,`（与既有的 `entra_fetch` 同一个模式：
都是「只为测试注入的出网替身」）。并在函数体开头附近加一句注释：

```python
    # http_get 只为测试注入。默认 None → catalog 用它自己的 _default_get。
    # 真实出网收在那一个函数里，测试永不触碰它。
```

2. 在 `_known_providers` 定义之后、`/settings` 路由之前，加一个内部函数：

```python
    def _fetch_catalog(provider: str) -> None:
        """拉一次目录并落库。**任何失败都不得让调用方失败**——
        保存 key 是主动作，拉目录是搭便车的那一个。
        """
        from framework_reader.llm.catalog import CatalogError, fetch_models
        from framework_reader.llm.config import effective_registry

        registry, _ = effective_registry(config=models_config)
        try:
            preset = registry.preset(provider)
        except Exception:  # noqa: BLE001 —— 厂商刚被删掉之类
            return
        key = models_config.key(provider)
        if not key:
            return
        try:
            models = fetch_models(preset, key, http_get=http_get)
        except CatalogError as exc:
            models_config.set_catalog(provider, [], error=str(exc))
            return
        except Exception:  # noqa: BLE001
            models_config.set_catalog(
                provider, [], error=f"拉 {provider} 的模型目录时出了意外，可以点刷新重试。")
            return
        models_config.set_catalog(provider, models)
```

3. 在 `models_key` 路由里，`identity.log("model.key", actor=_who(request), detail=f"配置 {provider}")` 这一行**之后**、`return RedirectResponse(...)` 之前，插入：

```python
        # 配完就顺手问一次「你这儿有哪些模型」。失败不影响 key 已经存好这件事。
        _fetch_catalog(provider)
```

4. 在 `models_provider_delete` 路由**之后**，新增：

```python
    @app.post("/models/catalog/refresh")
    @needs(perm.MODEL_WRITE)
    def models_catalog_refresh(request: Request, provider: str = Form("")):
        if provider not in _known_providers():
            return _models_page(error=f"没有这个厂商：{provider}", status=400)
        if not models_config.key(provider):
            return _models_page(
                error=f"{provider} 还没配 key，先配 key 再拉目录。", status=400)
        _fetch_catalog(provider)
        return RedirectResponse("/models", status_code=303)
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/web/test_model_catalog.py -q`
Expected: 「保存 key 就拉」「失败不阻断」这些过；`test_the_models_show_up_as_a_dropdown`、`test_the_page_says_when_it_was_fetched`、`test_the_page_explains_why_there_is_no_dropdown` 仍红——页面还没渲，Task 4 做。

- [ ] **Step 5: 提交**

```bash
git add src/framework_reader/web/app.py tests/web/test_model_catalog.py
git commit -m "feat(web): 保存 key 之后自动拉一次模型目录

自动拉取只发生在保存 key 那一刻，页面加载不触发出网；另有一条 /models/catalog/refresh
手动刷新。任何失败都不得让保存 key 失败——有些厂商的 /models 要额外权限，
而同一把 key 跑 chat 完全没问题。

create_app 加 http_get 关键字参数，只为测试注入；真实出网仍收在
catalog._default_get 那一个函数里。"
```

---

### Task 4: 模型名一栏改成「下拉 + 手填」

**Files:**
- Modify: `src/framework_reader/web/views.py`（`models()` 的角色块与厂商一览表）
- Modify: `src/framework_reader/web/app.py`（`_models_page` 多传一个 `catalogs`）
- Test: `tests/web/test_model_catalog.py`（Task 3 已写好，这一步让剩下三条转绿）

**Interfaces:**
- Consumes: Task 2 的 `catalogs()`
- Produces: `views.models(...)` 新增关键字参数 `catalogs: dict | None = None`

- [ ] **Step 1: 确认还红的是哪三条**

Run: `.venv/bin/python -m pytest tests/web/test_model_catalog.py -q`
Expected: `test_the_models_show_up_as_a_dropdown`、`test_the_page_says_when_it_was_fetched`、`test_the_page_explains_why_there_is_no_dropdown` 三条 FAIL

- [ ] **Step 2: 再加两条测试（一览表那两列）**

追加到 `tests/web/test_model_catalog.py`：

```python
def test_the_overview_keeps_our_verification_and_yours_apart(ok):
    """「未验活」= 我们没 ping 过（预设属性）；「已通」= 你的 key 拉通了（运行时事实）。
    混成一列，三个月后没人说得清它在说谁。"""
    client, _, _ = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = client.get("/models").text
    assert "我们验过" in page and "你的 key" in page


def test_a_provider_without_a_key_has_no_catalog_column_content(ok):
    client, _, _ = ok
    page = client.get("/models").text
    assert "groq" in page          # 预设仍然列着
    assert "deepseek-reasoner" not in page   # 但没有谁的模型清单
```

- [ ] **Step 3: 写实现**

在 `src/framework_reader/web/app.py` 的 `_models_page` 里，`views.models(` 调用中 `custom=custom,` 那一行之后加：

```python
            catalogs=models_config.catalogs(),
```

在 `src/framework_reader/web/views.py`：

1. `models()` 签名里 `custom: dict | None = None,` 之后加 `catalogs: dict | None = None,`

2. 在 `def _options(chosen: str) -> str:` 之前插入：

```python
    catalogs = catalogs or {}

    def _model_field(role_name: str, provider: str, current_model: str) -> str:
        """下拉 + 手填。**下拉是便利，不是唯一入口**——新模型上线永远早于任何目录，
        自定义端点与内网网关也未必有这个接口。

        没拉过 / 拉失败 / 这家不支持时，只显示手填框加一句原因，
        **不显示一个空下拉**——空控件让人以为坏了。
        """
        cached = catalogs.get(provider)
        hint, picker = "", ""
        if cached is None:
            hint = "配好这家的 key 之后会自动拉一次可用模型。"
        elif cached["error"]:
            hint = escape(cached["error"])
        elif cached["models"]:
            picker = (
                '<select class="pick" name="model_pick">'
                '<option value="">（从目录里选）</option>'
                + "".join(
                    f'<option value="{escape(m)}"'
                    f'{" selected" if m == current_model else ""}>{escape(m)}</option>'
                    for m in cached["models"])
                + "</select>"
            )
            hint = (f'取自 {escape(provider)}，{escape(cached["fetched_at"][:16])} 拉取')
        else:
            hint = f"{escape(provider)} 的目录是空的，模型名手填。"

        refresh = ""
        if can_write and catalogs.get(provider) is not None:
            refresh = (
                f'<button type="submit" form="refresh-{escape(provider)}" '
                'class="linky">刷新</button>')
        return (
            f"<label>模型名</label>{picker}"
            f'<input type="text" name="model" value="{escape(current_model)}"'
            ' placeholder="也可以直接手填">'
            f'<p class="hint">{hint} {refresh}</p>'
        )
```

3. 角色块里这两行（`src/framework_reader/web/views.py:1024-1025`）逐字是：

```python
                f'<div><label>模型名</label><input type="text" name="model" '
                f'value="{escape(model)}" required></div>'
```

替换为：

```python
                f'<div>{_model_field(name, provider, model)}</div>'
```

注意 `required` 一并去掉：下拉选了之后手填框可以是空的，留着 `required`
会让「用下拉选」这条路提交不了。两个都空的情况由路由报错（既有行为，不改）。

**`model_pick` 有值时要优先于 `model`**——在 `app.py` 的 `models_role` 路由里，
把签名加上 `model_pick: str = Form("")`，并在函数体第一行加：

```python
        # 下拉选了就用下拉的；下拉留空表示「我手填」。两个都空才报错。
        model = model_pick.strip() or model
```

4. 每个角色块的表单之后，为「刷新」按钮补一个隐藏表单（放在角色块 `</form>` 之后）：

```python
            f'<form id="refresh-{escape(provider)}" method="post" '
            f'action="/models/catalog/refresh" style="display:none">'
            f'<input type="hidden" name="provider" value="{escape(provider)}">'
            "</form>"
```

5. 厂商一览表：把原来的「验活」一列拆成两列。当前表头（`views.py:1095-1097`）逐字是：

```python
        '<table class="mtable"><tr><td><strong>厂商</strong></td>'
        "<td><strong>来源</strong></td><td><strong>验活</strong></td>"
        "<td><strong>说明</strong></td><td><strong>key</strong></td></tr>"
```

替换为：

```python
        '<table class="mtable"><tr><td><strong>厂商</strong></td>'
        "<td><strong>来源</strong></td><td><strong>我们验过</strong></td>"
        "<td><strong>你的 key</strong></td>"
        "<td><strong>说明</strong></td><td><strong>key</strong></td></tr>"
```

行渲染改成：

```python
    def _mine(pid: str) -> str:
        cached = catalogs.get(pid)
        if cached is None:
            return ""
        if cached["error"]:
            return "拉不到"
        return f"已通（{len(cached['models'])} 个模型）"

    overview_rows = "".join(
        f"<tr><td><code>{escape(p['id'])}</code></td>"
        f"<td>{'自定义' if p.get('custom') else '预设'}</td>"
        f"<td>{'未验活' if not p.get('verified', True) else '✓'}</td>"
        f"<td>{_mine(p['id'])}</td>"
        f"<td>{escape(p['note'])}</td>"
        f"<td>{'已配' if p['id'] in keys else ''}</td></tr>"
        for p in presets
    )
```

并把说明那段话改成：

```python
        '<p class="note">「我们验过」= 发内容包之前我们拿真 key ping 过这家；'
        "「你的 key」= 这台机器上用你配的 key 成功拉到了目录。"
        "**两件事**：一个是预设自带的属性，一个是此刻的运行时事实。</p>"
```

6. `_MODEL_CSS` 末尾追加：

```css
.linky{background:none;border:0;padding:0;color:var(--accent);
  font:inherit;font-size:.85rem;cursor:pointer;text-decoration:underline}
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿。若 `tests/web/test_models.py` 里断言旧表头的用例红了，按新的六列改断言——一览表从五列变六列是本任务的既定改动。

- [ ] **Step 5: 手工看一眼**

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN -t | xargs -r kill
.venv/bin/fr serve &
sleep 4 && curl -s http://127.0.0.1:8765/models | grep -o '我们验过\|你的 key\|从目录里选' | sort -u
```

Expected: 三个都出现

- [ ] **Step 6: 提交**

```bash
git add src/framework_reader/web/views.py src/framework_reader/web/app.py tests/web/test_model_catalog.py tests/web/test_models.py
git commit -m "feat(web): 模型名一栏改成「下拉 + 手填」

下拉是便利，不是唯一入口——新模型上线永远早于任何目录，自定义端点与内网网关
也未必有这个接口。没拉过／拉失败／目录为空时只显示手填框加一句原因，
不显示一个空下拉：空控件让人以为坏了。

厂商一览的「验活」拆成两列：「我们验过」是预设自带的属性，「你的 key」是
这台机器上此刻的运行时事实。混成一列，三个月后没人说得清它在说谁。"
```

---

### Task 5: 把出网路径的约定钉死

**Files:**
- Modify: `tests/test_no_network_in_tests.py`
- Modify: `README.md`（三条红线第 3 条）
- Modify: `docs/superpowers/specs/2026-08-19-framework-reader-design.md`（§10.A 红线三）

**Interfaces:**
- Consumes: Task 1 的 `catalog.py`
- Produces: 无（只是断言与文档）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_no_network_in_tests.py`：

```python
HTTPX_ALLOWED = {
    "openai_compat.py",      # chat，携带内容，被 GuardedClient 包住
    "anthropic_adapter.py",  # 同上
    "entra.py",              # OIDC 发现与换令牌，不携带内容
    "catalog.py",            # 模型目录，不携带内容
}


def test_only_the_declared_files_may_touch_httpx():
    """出网点必须是可数的。

    红线三真正要保的是**内容**不外流，不是「进程只准发一个请求」——
    见 2026-08-24 模型目录设计 §0。所以规矩不是「只许一个」，是
    「每一个都必须收在一个可注入替换的 _default_* 里，且清单写在这儿」。

    第五个文件里出现 httpx，这条就红。不靠人记得去翻 spec。
    """
    offenders = sorted(
        str(p) for p in Path("src").rglob("*.py")
        if "httpx" in p.read_text(encoding="utf-8") and p.name not in HTTPX_ALLOWED
    )
    assert offenders == [], f"这些文件碰了 httpx，但不在白名单里：{offenders}"
```

并把 `test_no_test_calls_the_live_check_command` 里的元组改成：

```python
        if any(w in text for w in ("_default_post", "_default_send",
                                   "_default_fetch", "_default_get"))
```

- [ ] **Step 2: 跑一遍**

Run: `.venv/bin/python -m pytest tests/test_no_network_in_tests.py -q`
Expected: PASS（Task 1 已经把 httpx 放在 `catalog.py` 里了；这一步是把约定钉死，不是修 bug）。若 FAIL，说明有文件在白名单之外用了 httpx——按提示查。

- [ ] **Step 3: 改两处文档**

`README.md` 「三条不可越过的红线」第 3 条，整条替换为：

```markdown
3. **Tier C/D 原文不得进入任何模型调用的 payload。**
   所有 chat client 由 `llm/registry.py` 组装并被 `GuardedClient` 包住——
   **凡是携带内容（控制条款、解读、配套文档节选）的出网只有这一条路径。**
   不携带任何内容的出网另有两条：身份 OIDC（`identity/entra.py`）与
   模型目录查询（`llm/catalog.py`）。它们各自独立，但都必须把真实请求收在
   一个可注入替换的 `_default_*` 函数里，且列在
   `tests/test_no_network_in_tests.py` 的白名单里——第五个文件碰 httpx 就红。
```

`docs/superpowers/specs/2026-08-19-framework-reader-design.md` 的 §10.A 红线三，在原文之后追加：

```markdown
> **2026-08-24 更正措辞。** 原文「出网只有这一条路径」在 2026-08-23 就已经不精确
> ——`identity/entra.py` 的 OIDC 那时就是第二条。红线要保的是**内容**不外流，
> 不是「进程只准发一个请求」。现行措辞与白名单见
> `docs/superpowers/specs/2026-08-24-model-catalog-design.md` §0。
```

- [ ] **Step 4: 全量跑一遍**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add tests/test_no_network_in_tests.py README.md docs/superpowers/specs/2026-08-19-framework-reader-design.md
git commit -m "test: 出网点必须可数——httpx 白名单断言

红线三要保的是内容不外流，不是「进程只准发一个请求」。规矩因此不是「只许一个」，
是「每一个都必须收在可注入替换的 _default_* 里，且清单写在测试里」。
第五个文件碰 httpx 就红，不靠人记得去翻 spec。

README 与主 spec §10.A 的措辞一并更正：原话在 2026-08-23 就已经不精确，
identity/entra.py 的 OIDC 那时就是第二条出网路径。"
```
