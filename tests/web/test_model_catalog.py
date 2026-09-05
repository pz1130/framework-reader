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
    page = client.get("/frameworks").text
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
    assert "Fetched" in client.get("/models").text


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

class _HttpStatus(Exception):
    """假的 HTTP 状态异常，形状与 httpx.HTTPStatusError 对齐。

    **不能直接抛 CatalogError**：`fetch_models` 会把任何异常按状态码重新翻译，
    没有 `.response` 的一律归成 unreachable——那样三种失败测出来是同一种。
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()


def _failing(kind):
    def http_get(url, headers):
        if kind == "auth":
            raise _HttpStatus(401)
        if kind == "unsupported":
            raise _HttpStatus(404)
        raise TimeoutError("超时")
    return http_get


@pytest.mark.parametrize("kind", ["auth", "unsupported", "unreachable"])
def test_the_key_is_saved_even_when_the_fetch_fails(tmp_path, monkeypatch, kind):
    client, config = _make(tmp_path, monkeypatch, _failing(kind))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    assert config.key("deepseek") == KEY


@pytest.mark.parametrize("kind,says", [
    ("auth", "rejected this key"), ("unsupported", "provides no model catalog"),
    ("unreachable", "Could not reach")])
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


def test_the_overview_that_carried_both_is_gone(ok):
    """这两列原先住在「厂商一览」里——2026-08-26 那张表整块拿掉了。

    「我们验过」（预设属性）和「你的 key 此刻拉不拉得通」（运行时事实）
    是两件事，当初分成两列正是为了别混在一起说；现在两件都不在页面上。
    """
    client, _, _ = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = client.get("/models").text
    assert "Provider overview" not in page
    assert "verified by us" not in page


def test_a_provider_without_a_key_has_no_catalog_column_content(ok):
    client, _, _ = ok
    page = client.get("/models").text
    assert "groq" in page          # 预设仍然列着
    assert "deepseek-reasoner" not in page   # 但没有谁的模型清单


# ---------- key 就在选厂商的地方填（2026-08-24） ----------

def test_the_role_block_asks_for_a_key_when_that_vendor_has_none(ok):
    """原来 key 要滚到下面「API key」那一栏、再选一次同一个厂商才能填。
    同一件事分两处做，中间还要重选一遍。"""
    client, _, _ = ok
    page = client.get("/models").text
    assert 'name="key"' in page.split("<h2>API key</h2>")[0]


def test_saving_a_key_from_the_role_block_stores_it_and_fetches(ok):
    client, config, calls = ok
    _post(client, "/models/role", role="drafter", provider="openai",
          key=KEY, model="")
    assert config.key("openai") == KEY
    assert config.catalog("openai")["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert len(calls) == 1


def test_supplying_a_key_does_not_change_the_role_yet(ok):
    """模型名框里还是上一家的模型（deepseek-chat）。拿它去配 openai 是错的——
    先把目录拉回来，让人挑一个，再存。"""
    client, config, _ = ok
    _post(client, "/models/role", role="drafter", provider="openai",
          key=KEY, model="deepseek-chat")
    assert config.roles().get("drafter") is None


def test_after_the_key_is_saved_that_block_is_preselected_to_the_new_vendor(ok):
    """重渲之后 drafter 那一块要停在 openai 上、且模型目录是 openai 的——
    否则又得重选一遍厂商，等于没解决。"""
    import re

    client, _, _ = ok
    resp = _post(client, "/models/role", role="drafter", provider="openai",
                 key=KEY, model="")
    drafter_block = resp.text.split("questioner")[0]
    assert re.search(r'<input[^>]*name="provider"[^>]*value="openai"', drafter_block)
    assert "deepseek-reasoner" in drafter_block


def test_the_notice_says_how_many_models_came_back(ok):
    client, _, _ = ok
    resp = _post(client, "/models/role", role="drafter", provider="openai",
                 key=KEY, model="")
    assert "2 models came back" in resp.text


def test_saving_a_key_from_the_role_block_never_shows_it_back(ok):
    client, _, _ = ok
    resp = _post(client, "/models/role", role="drafter", provider="openai",
                 key=KEY, model="")
    assert KEY not in resp.text and "0123456789abcdef" not in resp.text


def test_without_a_key_the_role_still_changes_the_old_way(ok):
    """老路子不能断：key 从环境变量来的部署，角色照样要能改。"""
    client, config, _ = ok
    assert _post(client, "/models/role", role="drafter", provider="deepseek",
                 model="deepseek-reasoner").status_code == 303
    assert config.roles()["drafter"]["model"] == "deepseek-reasoner"


def test_a_vendor_that_already_has_a_key_gets_no_key_box(ok):
    client, _, _ = ok
    _post(client, "/models/key", provider="deepseek", key=KEY)
    drafter_block = client.get("/models").text.split("questioner")[0]
    assert 'name="key"' not in drafter_block
