"""管理员在网页上配模型与 key，以及花钱那三道闸。见网页服务化设计 §6⑤⑥、§8 S4

这一页是「用户接入自己的 AI」的正解：不是把 key 写进服务器的环境变量
（那要 shell，而且改一次要重启），是管理员在界面上填、加密落库、脱敏回显。
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


@pytest.fixture
def env(tmp_path, monkeypatch):
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
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                            roles=("author",))
    return type("Env", (), {
        "app": create_app(db), "identity": identity, "config": ModelConfig(),
    })()


def _as(env, email, password):
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": email, "password": password})
    return client


def _boss(env):
    return _as(env, "boss@acme.cn", "pw-boss-boss")


def _ann(env):
    return _as(env, "ann@acme.cn", "pw-ann-ann-ann")


def _post(client, path, **data):
    # 从主页取令牌，不从 /models 取：author 在那一页没有任何 POST 表单
    # （他不能改），拿不到令牌就会被 CSRF 拦下——那样测的是假的。
    page = client.get("/frameworks").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return client.post(path, data={"csrf": found.group(1) if found else "", **data})


# ---------- 谁能看、谁能改 ----------

def test_an_admin_sees_the_model_page(env):
    assert _boss(env).get("/models").status_code == 200


def test_an_author_can_see_which_model_will_spend_his_budget(env):
    assert _ann(env).get("/models").status_code == 200


def test_an_author_cannot_change_the_key(env):
    assert _post(_ann(env), "/models/key",
                 provider="deepseek", key=KEY).status_code == 403


def test_an_approver_has_no_business_here(env):
    env.identity.create_account(email="amy@acme.cn", password="pw-amy-amy-amy",
                                roles=("approver",))
    assert _as(env, "amy@acme.cn", "pw-amy-amy-amy").get(
        "/models").status_code == 403


# ---------- key ----------

def test_an_admin_can_set_a_key(env):
    _post(_boss(env), "/models/key", provider="deepseek", key=KEY)
    assert env.config.key("deepseek") == KEY


def test_the_page_never_shows_the_key_back(env):
    _post(_boss(env), "/models/key", provider="deepseek", key=KEY)
    page = _boss(env).get("/models").text
    assert KEY not in page
    assert "0123456789" not in page
    assert "cdef" in page                      # 认得出是不是上次那把就够了


def test_the_key_is_encrypted_at_rest(env):
    _post(_boss(env), "/models/key", provider="deepseek", key=KEY)
    assert "0123456789abcdef" not in env.config.path.read_bytes().decode(
        "utf-8", "ignore")


def test_the_key_never_lands_in_the_audit_log(env):
    _post(_boss(env), "/models/key", provider="deepseek", key=KEY)
    entries = env.identity.audit()
    assert any(e["event"] == "model.key" for e in entries)
    assert all(KEY not in (e["detail"] or "") for e in entries)


def test_a_key_can_be_cleared(env):
    client = _boss(env)
    _post(client, "/models/key", provider="deepseek", key=KEY)
    _post(client, "/models/key", provider="deepseek", clear="1")
    assert env.config.key("deepseek") is None


def test_a_provider_we_do_not_know_is_refused(env):
    """预设里没有的厂商，端点是空的——存了也只是一个用不了的 key。"""
    response = _post(_boss(env), "/models/key", provider="made-up", key=KEY)
    assert response.status_code == 400
    assert env.config.masked() == {}


def test_an_empty_key_is_not_stored_as_an_empty_key(env):
    response = _post(_boss(env), "/models/key", provider="deepseek", key="  ")
    assert response.status_code == 400
    assert env.config.masked() == {}


# ---------- 没有主密钥就不收 ----------

def test_without_a_master_key_the_page_says_so_before_you_type(env, monkeypatch):
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    assert "FR_SECRET_KEY" in _boss(env).get("/models").text


def test_without_a_master_key_the_key_is_refused_not_stored_in_the_clear(
        env, monkeypatch):
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    response = _post(_boss(env), "/models/key", provider="deepseek", key=KEY)
    assert response.status_code == 400
    assert "fr secret new" in response.text
    assert env.config.masked() == {}


# ---------- 角色 ----------

def test_an_admin_can_point_the_drafter_at_another_provider(env):
    _post(_boss(env), "/models/role", role="drafter", provider="qwen",
          model="qwen-max")
    assert env.config.roles()["drafter"]["provider"] == "qwen"


def test_changing_the_model_lands_in_the_audit_log(env):
    """换 endpoint = 数据流向变了。设计 §4.4"""
    _post(_boss(env), "/models/role", role="drafter", provider="qwen",
          model="qwen-max")
    assert any(e["event"] == "model.role" and "qwen" in e["detail"]
               for e in env.identity.audit())


def test_a_role_we_do_not_have_is_refused(env):
    assert _post(_boss(env), "/models/role", role="mayor", provider="qwen",
                 model="qwen-max").status_code == 400


# ---------- 限速与预算 ----------

def test_the_page_shows_this_month_against_the_cap(env):
    env.config.charge_draft("ann@acme.cn", 42, what="ACME-1", running_jobs=0)
    page = _boss(env).get("/models").text
    assert "42" in page


def test_an_admin_can_change_the_caps(env):
    _post(_boss(env), "/models/limits", draft_cap_hour="10",
          draft_cap_month="100", draft_max_jobs="1")
    assert env.config.limits()["draft_cap_month"] == 100


def test_a_zero_cap_is_refused_with_a_reason(env):
    response = _post(_boss(env), "/models/limits", draft_cap_hour="0",
                     draft_cap_month="100", draft_max_jobs="1")
    assert response.status_code == 400
    assert "author" in response.text


def test_changing_the_caps_is_audited(env):
    _post(_boss(env), "/models/limits", draft_cap_hour="10",
          draft_cap_month="100", draft_max_jobs="1")
    assert any(e["event"] == "model.limits" for e in env.identity.audit())


# ---------- 闸门真的挡得住 ----------

def _import_a_framework(controls: int = 3):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[(f"4.{i}", f"第 {i} 条", None, "正文") for i in range(controls)])


def test_a_draft_over_the_month_budget_runs_only_what_fits(env, monkeypatch):
    """800-53 一千多条，月预算 300 时切一刀接着跑，不许整趟拒掉。"""
    from framework_reader.web import jobs

    jobs.reset()
    monkeypatch.setattr(jobs, "start", lambda *a, **k: None)
    _import_a_framework(3)
    env.config.set_limits(draft_cap_month=2, by="boss@acme.cn")
    response = _post(_ann(env), "/f/ACME-1/draft")
    assert response.status_code == 303
    assert env.config.spent_this_month() == 2


def test_a_draft_inside_the_budget_still_runs(env, monkeypatch):
    from framework_reader.web import jobs

    _import_a_framework(3)
    jobs.reset()
    monkeypatch.setattr(jobs, "start", lambda *a, **k: None)
    assert _post(_ann(env), "/f/ACME-1/draft").status_code == 303


def test_a_draft_with_no_budget_left_is_refused(env, monkeypatch):
    from framework_reader.web import jobs

    jobs.reset()
    monkeypatch.setattr(jobs, "start", lambda *a, **k: None)
    _import_a_framework(3)
    env.config.set_limits(draft_cap_month=2, by="boss@acme.cn")
    assert _post(_ann(env), "/f/ACME-1/draft").status_code == 303
    response = _post(_ann(env), "/f/ACME-1/draft")
    assert response.status_code == 429
    assert "budget" in response.text
    assert env.config.spent_this_month() == 2


def test_the_refusal_names_the_number_so_the_admin_can_act(env, monkeypatch):
    from framework_reader.web import jobs

    jobs.reset()
    monkeypatch.setattr(jobs, "start", lambda *a, **k: None)
    _import_a_framework(3)
    env.config.set_limits(draft_cap_month=2, by="boss@acme.cn")
    _post(_ann(env), "/f/ACME-1/draft")
    body = _post(_ann(env), "/f/ACME-1/draft").text
    assert "2" in body and "administrator" in body


def test_one_control_at_a_time_is_charged_too(env, monkeypatch):
    from framework_reader.web import jobs

    _import_a_framework(3)
    jobs.reset()
    monkeypatch.setattr(jobs, "start", lambda *a, **k: None)
    _post(_ann(env), "/c/ACME-1:4.1/draft")
    assert env.config.spent_this_month() == 1


def test_the_page_shows_what_is_actually_in_use_not_just_what_was_clicked(env):
    """这一页要回答的是「现在到底谁在收我们的钱」，
    而不是「我在这儿点过什么」——没配过的角色不能显示成一片空白。"""
    page = _boss(env).get("/models").text
    assert "deepseek" in page                 # 内置预设里 drafter 就是它
    assert "built-in preset" in page


def test_the_description_is_not_raw_markdown(env):
    page = _boss(env).get("/models").text
    assert "**" not in page


# ---------- 自定义端点（2026-08-24） ----------

def test_an_admin_can_add_a_custom_endpoint(env):
    r = _post(_boss(env), "/models/provider", provider="corp-gw",
              base_url="https://gw.acme.cn/v1", default_model="qwen2.5-72b")
    assert r.status_code == 303
    assert env.config.custom_providers()["corp-gw"]["base_url"] == "https://gw.acme.cn/v1"


def test_an_author_cannot_add_a_custom_endpoint(env):
    """加端点 = 决定数据发往哪里。那是 admin 的事，不是花钱的人的事。"""
    assert _post(_ann(env), "/models/provider", provider="corp-gw",
                 base_url="https://gw.acme.cn/v1",
                 default_model="m").status_code == 403


def test_a_public_http_endpoint_is_refused_with_a_reason(env):
    r = _post(_boss(env), "/models/provider", provider="leaky",
              base_url="http://api.example.com/v1", default_model="m")
    assert r.status_code == 400
    assert "http://" in r.text and "internal networks" in r.text
    assert "leaky" not in env.config.custom_providers()


def test_a_custom_endpoint_can_be_chosen_for_drafting(env):
    _post(_boss(env), "/models/provider", provider="corp-gw",
          base_url="https://gw.acme.cn/v1", default_model="qwen2.5-72b")
    r = _post(_boss(env), "/models/role", role="drafter",
              provider="corp-gw", model="qwen2.5-72b")
    assert r.status_code == 303
    assert env.config.roles()["drafter"]["provider"] == "corp-gw"


def test_deleting_an_endpoint_in_use_is_refused(env):
    _post(_boss(env), "/models/provider", provider="corp-gw",
          base_url="https://gw.acme.cn/v1", default_model="m")
    _post(_boss(env), "/models/role", role="drafter", provider="corp-gw", model="m")
    r = _post(_boss(env), "/models/provider/delete", provider="corp-gw")
    assert r.status_code == 400
    assert "drafter" in r.text
    assert "corp-gw" in env.config.custom_providers()


def test_the_endpoint_shows_up_on_the_page(env):
    _post(_boss(env), "/models/provider", provider="corp-gw",
          base_url="https://gw.acme.cn/v1", default_model="qwen2.5-72b")
    page = _boss(env).get("/models").text
    assert "corp-gw" in page and "https://gw.acme.cn/v1" in page


def test_the_audit_log_records_where_the_data_will_go_but_never_the_key(env):
    """换 endpoint = 数据流向变了，必须留痕（设计 §4.4）。key 一个字符不进日志。"""
    _post(_boss(env), "/models/provider", provider="corp-gw",
          base_url="https://gw.acme.cn/v1", default_model="m")
    _post(_boss(env), "/models/key", provider="corp-gw", key=KEY)
    rows = [r for r in env.identity.audit() if r["event"].startswith("model.")]
    detail = " ".join(r["detail"] or "" for r in rows)
    assert "https://gw.acme.cn/v1" in detail
    assert KEY not in detail and KEY[3:] not in detail


# ---------- 下拉框不是放句子的地方（2026-08-24） ----------

def test_the_provider_dropdown_carries_only_the_id(env):
    """`<option>` 里塞整句说明，浏览器会按最长那条撑开下拉框——
    实测盖住半个屏幕，最长那条还被截成「不建议用作…」。
    选项只放编号。"""
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    assert '<option value="minimax"' in picker
    assert "起草质量实测不合格" not in picker


def test_a_custom_endpoint_is_marked_in_the_dropdown(env):
    """自定义端点和预设混在一列里，得看得出哪个是自己加的。"""
    _post(_boss(env), "/models/provider", provider="corp-gw",
          base_url="https://gw.acme.cn/v1", default_model="m")
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    found = re.search(r'<option value="corp-gw"[^>]*>([^<]*)</option>', picker)
    assert found, "自定义端点没进厂商列表"
    # 正文要能看出是哪一家**并且**是自己加的；value 保持干净，
    # 否则选一下就把「自定义端点」这几个字提交上去。
    assert "corp-gw" in found.group(1) and "custom" in found.group(1)


def test_the_provider_overview_table_is_gone(env):
    """2026-08-26：那张 20 行的「厂商一览」按要求整块拿掉。

    连带没了的是每家的 note（含 MiniMax 那句「起草质量实测不合格」）、
    「我们验过」与「你的 key」两列。
    """
    page = _boss(env).get("/models").text
    assert "Provider overview" not in page
    assert "verified by us" not in page
    assert "起草质量实测不合格" not in page


def _dropdowns(html: str) -> str:
    import re
    return " ".join(re.findall(r"<datalist.*?</datalist>", html, re.S))


# ---------- 下拉不再用原生 select ----------
#
# macOS 的原生 `<select>` 展开时是系统菜单（NSMenu），字号由系统菜单字体决定，
# 网页 CSS 碰不到——闭合态 14.4px、展开态约 19px，比正文还大一圈。
# 换成 `<input list>` + `<datalist>`：下拉由浏览器画在页面内，字号跟着 input 走。

def _role_blocks(html: str) -> str:
    """按**角色表单**切，不按 `<div class="mrow">` 切。

    原来那个正则是 `<div class="mrow">.*?</div>`，非贪婪停在第一个 `</div>`
    上——几乎什么都没截到。靠它「断言某样东西不存在」的测试全是空转绿的。
    """
    import re

    found = re.findall(
        r'<form method="post" action="/models/role">.*?</form>', html, re.S)
    assert found, "页面上没有角色表单"
    return " ".join(found)


def test_the_model_name_is_never_a_native_select(env):
    """模型名是**开放集合**——新模型永远早于任何目录，自定义端点也未必有
    那个接口。所以它必须能手填，datalist 留在这儿。"""
    import re

    blocks = _role_blocks(_boss(env).get("/models").text)
    assert re.search(r'<input[^>]*name="model"', blocks)
    assert not re.search(r'<select[^>]*name="model"', blocks)


def test_every_provider_is_still_offered(env):
    """断言在**厂商 select** 里——`<option>` 满页都是，
    光找它会得到一个永远绿的测试。"""
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    assert 'value="qwen"' in picker
    assert 'value="anthropic"' in picker


def test_the_provider_in_use_is_preselected(env):
    """不预选的话，「改这个角色」一点就把厂商换成了别的，
    而点的人以为自己只改了模型名。
    """
    import re

    env.config.set_role("drafter", provider="qwen", model="qwen-max",
                        by="boss@acme.cn")
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    assert re.search(r'<option value="qwen"[^>]*\sselected', picker)


def test_the_model_box_is_one_field_not_two(env):
    """原来是「下拉选一个」加「或者手填」两个控件，值还要在服务端二选一。

    datalist 天生就是这两件事的同一个控件：能选，也能填。
    """
    env.config.set_catalog("deepseek", ["deepseek-chat", "deepseek-reasoner"])
    page = _boss(env).get("/models").text
    assert "deepseek-reasoner" in page   # 目录确实渲出来了
    assert "model_pick" not in page


def test_the_key_form_picks_its_provider_from_a_list_too(env):
    """底下「API key」那一栏也有一个厂商下拉。漏掉它，同一个抱怨会再来一次。"""
    import re

    page = _boss(env).get("/models").text
    form = re.search(r'<form method="post" action="/models/key">.*?</form>',
                     page, re.S).group(0)
    assert "<select" in form and 'value="qwen"' in form


# ---------- 框里有字的时候也得能换厂商（2026-08-26） ----------
#
# 抱怨原话：「模型供应商里面有字段的话，倒三角就点不到，一定要删除现有
# 字段才能选」。`<input list>` 的候选**按当前值过滤**——那是 datalist 的
# 设计意图（开放集合的建议），不是它的毛病，是控件选错了：厂商是封闭集合，
# 服务端 `_known_providers()` 硬校验，填错当场退回。封闭集合就该用 select。


def test_the_whole_list_is_there_even_when_one_is_already_chosen(env):
    import re

    env.config.set_role("drafter", provider="qwen", model="qwen-max",
                        by="boss@acme.cn")
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    for pid in ("qwen", "anthropic", "deepseek", "minimax"):
        assert f'value="{pid}"' in picker, pid
    assert re.search(r'<option value="qwen"[^>]*\sselected', picker)


def test_a_provider_that_is_no_longer_offered_is_kept_and_flagged(env):
    """自定义端点被删掉、或预设改了名之后。

    `<select>` 总会提交点什么。把认不出的值悄悄扔掉，等于替人改了配置——
    而他只是想看看这一页。
    """
    import re

    env.config.set_role("drafter", provider="ghost-gw", model="m",
                        by="boss@acme.cn")
    picker = _provider_picker(_role_blocks(_boss(env).get("/models").text))
    assert re.search(r'<option value="ghost-gw"[^>]*\sselected', picker)
    assert "stale" in picker


def _provider_picker(html: str) -> str:
    import re

    found = re.search(r'<select[^>]*name="provider".*?</select>', html, re.S)
    assert found, "角色块里没有厂商 select"
    return found.group(0)


def test_the_shared_provider_datalist_is_gone(env):
    """厂商改用 select 之后它没人引用了。

    孤儿 datalist 不会报错，只会让下一个读这段代码的人以为厂商还能手填。
    """
    assert 'id="providers"' not in _boss(env).get("/models").text
