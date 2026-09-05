"""「设置」这一页，以及它要修的那个入口 bug。

`/models` 一直是**界面上到不了的**：那条链接被 `not logged_in()` 挡着。
那个判断的本意写在注释里——「本机单人用法下没有『成员』这回事」——
对**成员**成立，对**模型与 key** 不成立：本机单人恰恰是最需要配自己 key 的
那种用法。一条判断被顺手复制到了第二个链接上，于是这一页只有知道地址的人
才进得去。能在地址栏做而界面上做不到的事，等于没做。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from pathlib import Path

from framework_reader import crypto
from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


def _content(path):
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    return path


@pytest.fixture
def solo(tmp_path, monkeypatch):
    """本机单人：一个账号都没有，不要求登录。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    return TestClient(create_app(_content(tmp_path / "content.sqlite")))


@pytest.fixture
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                            roles=("author",))
    identity.create_account(email="vic@acme.cn", password="pw-vic-vic-vic",
                            roles=("viewer",))
    app = create_app(_content(tmp_path / "content.sqlite"))

    def as_(email, password):
        client = TestClient(app, follow_redirects=False)
        client.post("/login", data={"email": email, "password": password})
        return client

    return as_


# ---------- 本机单人 ----------

def test_the_settings_button_is_in_the_top_bar(solo):
    assert "Settings" in _topnav(solo.get("/frameworks").text)


def test_the_model_page_is_reachable_without_logging_in(solo):
    """这是本次要修的那个 bug。"""
    page = solo.get("/settings").text
    assert solo.get("/settings").status_code == 200
    assert "/models" in page


def test_user_management_is_reachable_before_anybody_has_an_account(solo):
    """入口原先被 `logged_in()` 挡着，理由是「本机单人没有成员这回事」。

    可那正是**建第一个管理员**的那一刻——把入口藏起来，等于要求人先去终端
    跑 `fr account invite` 才能在界面上管人。于是这一页对本机用户来说，
    「用户管理」这个功能根本不存在。
    """
    page = solo.get("/settings").text
    assert "/members" in page
    assert "Create the first administrator" in page


def test_the_top_bar_no_longer_carries_the_three_direct_links(solo):
    nav = _topnav(solo.get("/frameworks").text)
    assert "Audit log" not in nav and "Models" not in nav and "Members" not in nav
    # 干活时要用的那两个留在顶栏：它们是内容，不是配置
    assert "Import framework" in nav and "Documents" in nav


# ---------- 按角色 ----------

def test_an_admin_sees_all_three(org):
    page = org("boss@acme.cn", "pw-boss-boss").get("/settings").text
    for href in ("/models", "/members", "/audit", "/settings/backup"):
        assert href in page, href


def test_an_author_sees_the_model_page_but_not_the_audit_log(org):
    """author 有 model:read（他要知道花的是哪家的钱），没有 audit:read。"""
    page = org("ann@acme.cn", "pw-ann-ann-ann").get("/settings").text
    assert "/models" in page and "/members" in page
    assert "/audit" not in page
    assert "/settings/backup" in page


def test_a_viewer_gets_in_but_finds_almost_nothing(org):
    """viewer 只有 member:read。进得去，但里面没有他能碰的东西。"""
    client = org("vic@acme.cn", "pw-vic-vic-vic")
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "/models" not in resp.text and "/audit" not in resp.text
    assert "/settings/backup" not in resp.text


# ---------- 老地址不能断 ----------

@pytest.mark.parametrize("path", ["/models", "/members", "/audit"])
def test_the_old_addresses_still_work(org, path):
    """书签、以及审计日志里贴过的链接。"""
    assert org("boss@acme.cn", "pw-boss-boss").get(path).status_code == 200


def _topnav(html: str) -> str:
    return " ".join(re.findall(r'class="topnav"[^>]*>([^<]*)', html))


# ---------- Single sign-on (Entra ID) 设置 ----------

def _csrf(client):
    page = client.get("/settings").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return found.group(1) if found else ""


_SSO_FORM = {
    "tenant_id": "11111111-2222-3333-4444-555555555555",
    "client_id": "app-client-1",
    "client_secret": "sec-123",
    "redirect_uri": "https://fw.acme.cn/auth/entra/callback",
    "authority": "",
    "enabled": "on",
}


def test_the_sso_and_branding_cards_are_admin_only(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    page = boss.get("/settings").text
    assert '/settings/sso' in page and '/settings/branding' in page
    viewer = org("vic@acme.cn", "pw-vic-vic-vic")
    page = viewer.get("/settings").text
    assert "/settings/sso" not in page and "/settings/branding" not in page


def test_sso_routes_refuse_non_admin(org):
    author = org("ann@acme.cn", "pw-ann-ann-ann")
    assert author.get("/settings/sso").status_code == 403
    assert author.post("/settings/sso", data={"csrf": _csrf(author)}).status_code == 403


def test_sso_save_persists_config_and_seals_the_secret(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    result = boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM},
                       follow_redirects=False)
    assert result.status_code == 303
    from framework_reader.identity.store import IdentityStore

    saved = IdentityStore().sso_config()
    assert saved["tenant_id"] == "11111111-2222-3333-4444-555555555555"
    assert saved["client_id"] == "app-client-1"
    assert saved["enabled"] and saved["has_secret"]
    # secret 密文落库：库文件里翻不到明文。
    from framework_reader import usage

    raw = (usage.home() / "identity.sqlite").read_bytes()
    assert b"sec-123" not in raw
    assert IdentityStore().sso_secret() == "sec-123"


def test_sso_save_with_blank_secret_keeps_the_saved_one(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM})
    boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM,
                                     "client_secret": ""})
    from framework_reader.identity.store import IdentityStore

    assert IdentityStore().sso_secret() == "sec-123"


def test_sso_empty_form_does_not_wipe_a_saved_config(org):
    """授权矩阵拿空表单打每一条路由——那一下不许把好配置抹了。"""
    boss = org("boss@acme.cn", "pw-boss-boss")
    boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM})
    boss.post("/settings/sso", data={"csrf": _csrf(boss)})
    from framework_reader.identity.store import IdentityStore

    assert IdentityStore().sso_config()["tenant_id"].startswith("1111")


def test_saving_sso_puts_the_company_button_on_the_login_page(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    assert "Sign in with your company account" not in boss.get("/login").text
    boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM})
    assert "Sign in with your company account" in boss.get("/login").text


def test_disabling_sso_removes_the_button(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    boss.post("/settings/sso", data={"csrf": _csrf(boss), **_SSO_FORM})
    boss.post("/settings/sso/disable", data={"csrf": _csrf(boss)},
              follow_redirects=False)
    from framework_reader.identity.store import IdentityStore

    assert IdentityStore().sso_config() is None
    assert "Sign in with your company account" not in boss.get("/login").text


def test_sso_check_reports_a_reached_discovery_document(org, monkeypatch):
    """发现文档打在替身 IdP 上；测试不出网（tests/test_no_network_in_tests.py）。"""
    import framework_reader.identity.entra as entra

    class _FakeIdP:
        def __init__(self, config, fetch=None):
            self.config = config

        def discovery(self):
            return {"issuer": "https://sts.windows.net/acme/"}

    monkeypatch.setattr(entra, "EntraClient", _FakeIdP)
    boss = org("boss@acme.cn", "pw-boss-boss")
    page = boss.post("/settings/sso/check",
                     data={"csrf": _csrf(boss), **_SSO_FORM}).text
    assert "Discovery document reached" in page
    assert "sts.windows.net/acme" in page


def test_sso_check_names_the_broken_field(org, monkeypatch):
    import framework_reader.identity.entra as entra

    class _DownIdP:
        def __init__(self, config, fetch=None):
            pass

        def discovery(self):
            raise entra.EntraError(
                "Cannot reach the company login service. "
                "Try again later, or sign in with your email and password.")

    monkeypatch.setattr(entra, "EntraClient", _DownIdP)
    boss = org("boss@acme.cn", "pw-boss-boss")
    form = {**_SSO_FORM, "redirect_uri": "http://insecure/callback"}
    page = boss.post("/settings/sso/check", data={"csrf": _csrf(boss), **form}).text
    assert "not https" in page
    # 两类问题同屏：字段体检 + 发现文档拉取失败（EntraClient 对外只给
    # 安全话，不给底层网络细节——那是它自己的设计）。
    assert "Discovery document" in page


def test_enabled_sso_locks_the_door_even_with_no_accounts(tmp_path, monkeypatch):
    """锁门状态不能吃启动快照：设置里存了 SSO，本机单人模式也立刻锁门。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    from framework_reader.identity.store import IdentityStore

    IdentityStore().save_sso_config(
        tenant_id="t-1", client_id="c-1", redirect_uri="https://x/auth/entra/callback",
        enabled=True, by="ops")
    from framework_reader.web.app import create_app

    client = TestClient(create_app(_content(tmp_path / "content.sqlite")),
                        follow_redirects=False)
    result = client.get("/")
    assert result.status_code == 303 and "/login" in result.headers["location"]


# ---------- 自定义 logo ----------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_branding_upload_serves_and_brands_the_top_bar(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    assert "No custom logo" in boss.get("/settings/branding").text
    result = boss.post("/settings/branding",
                       files={"file": ("logo.png", _PNG, "image/png")},
                       data={"csrf": _csrf(boss)}, follow_redirects=False)
    assert result.status_code == 303
    logo = boss.get("/branding/logo")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
    home = boss.get("/frameworks").text
    assert 'class="brandlogo" src="/branding/logo?v=' in home


def test_the_logo_route_is_public_without_login(solo):
    """登录页（裸页）也显示 logo——没登录也得拿得到这张图、传得上传。
    solo 模式没登录、没有会话 cookie：能上传、能取图，才证明路由真的公开。"""
    solo.post("/settings/branding",
              files={"file": ("logo.png", _PNG, "image/png")})
    assert solo.get("/branding/logo").status_code == 200
    assert solo.get("/branding/logo").headers["content-type"] == "image/png"
    assert 'class="brandlogo"' in solo.get("/login").text


def test_branding_remove_restores_the_text_brand(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    boss.post("/settings/branding",
              files={"file": ("logo.png", _PNG, "image/png")}, data={"csrf": _csrf(boss)})
    boss.post("/settings/branding/remove", data={"csrf": _csrf(boss)})
    assert boss.get("/branding/logo").status_code == 404
    assert 'class="brandlogo"' not in boss.get("/frameworks").text
    assert ">Framework Workbench</a>" in boss.get("/frameworks").text


def test_branding_accepts_a_clean_svg(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    clean = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
             b'<rect width="8" height="8" fill="red"/></svg>')
    result = boss.post("/settings/branding",
                       files={"file": ("logo.svg", clean, "image/svg+xml")},
                       data={"csrf": _csrf(boss)}, follow_redirects=False)
    assert result.status_code == 303
    logo = boss.get("/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/svg+xml"
    # 伺服带 default-src 'none' 的 CSP：直接在地址栏打开也跑不了脚本。
    assert "default-src 'none'" in logo.headers.get("content-security-policy", "")
    assert b"<rect" in logo.content


def test_branding_strips_event_handlers_and_external_refs(org):
    """属性级的危险件（on* / 外链 href）进场前剥干净，图形正文留着。"""
    boss = org("boss@acme.cn", "pw-boss-boss")
    hostile = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
               b'<rect width="8" height="8" fill="blue" onload="alert(2)"/>'
               b'<a href="https://evil.example"><path d="M0 0"/></a></svg>')
    result = boss.post("/settings/branding",
                       files={"file": ("logo.svg", hostile, "image/svg+xml")},
                       data={"csrf": _csrf(boss)}, follow_redirects=False)
    assert result.status_code == 303
    served = boss.get("/branding/logo")
    assert served.status_code == 200
    body = served.content
    assert b"onload" not in body
    assert b"evil.example" not in body
    assert b"<rect" in body


def test_branding_refuses_a_hostile_or_broken_svg(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    scripted = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                b"<script>alert(1)</script></svg>")
    result = boss.post("/settings/branding",
                       files={"file": ("logo.svg", scripted, "image/svg+xml")},
                       data={"csrf": _csrf(boss)})
    assert result.status_code == 400 and "forbidden element: script" in result.text
    broken = b'<svg xmlns="http://www.w3.org/2000/svg"><rect</svg>'
    result = boss.post("/settings/branding",
                       files={"file": ("logo.svg", broken, "image/svg+xml")},
                       data={"csrf": _csrf(boss)})
    assert result.status_code == 400 and "well-formed" in result.text


def test_branding_refuses_oversize_and_empty(org):
    boss = org("boss@acme.cn", "pw-boss-boss")
    result = boss.post("/settings/branding",
                       files={"file": ("big.png", _PNG + b"x" * (512 * 1024), "image/png")},
                       data={"csrf": _csrf(boss)})
    assert result.status_code == 400 and "512 KB" in result.text
    result = boss.post("/settings/branding", data={"csrf": _csrf(boss)})
    assert result.status_code == 400 and "Choose a file" in result.text


def test_sso_save_without_a_master_key_is_refused_not_500(solo, monkeypatch):
    """和模型 key 同一条规矩：没配主密钥就拒绝落库，绝不悄悄明文存。"""
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    result = solo.post("/settings/sso", data={
        "tenant_id": "t-1", "client_id": "c-1", "client_secret": "s-1",
        "redirect_uri": "https://x/auth/entra/callback"})
    assert result.status_code == 400
    assert "FR_SECRET_KEY" in result.text


def test_branding_keeps_an_svg_that_embeds_a_bitmap(org):
    """设计工具导出的 logo 常是「SVG 外壳 + base64 位图」——data:image
    的 href 是图的本体，剥了它整张图就空白。外链仍然要剥。"""
    boss = org("boss@acme.cn", "pw-boss-boss")
    logo = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
            b'xmlns:xlink="http://www.w3.org/1999/xlink" width="4" height="4">'
            b'<image width="4" height="4" xlink:href="data:image/png;base64,AAAA"/>'
            b'<image width="4" height="4" xlink:href="https://evil.example/x.png"/>'
            b"</svg>")
    result = boss.post("/settings/branding",
                       files={"file": ("logo.svg", logo, "image/svg+xml")},
                       data={"csrf": _csrf(boss)}, follow_redirects=False)
    assert result.status_code == 303
    served = boss.get("/branding/logo")
    assert served.status_code == 200
    assert b"data:image/png;base64,AAAA" in served.content
    assert b"evil.example" not in served.content
