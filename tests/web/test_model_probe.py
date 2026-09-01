"""「测一下」按钮：不保存，只回答「这组配置此刻能不能用」。

**测的是表单里此刻选的那组，不是库里存着的那组。** 顺序因此是
「选 → 测 → 存」：不用先存一个没验证过的配置，再回头验它。

它同时把一个死路解开了：模型下拉只在「该角色当前的厂商」有目录时才渲出来，
而换厂商要提交，提交又要求模型名非空——模型名本该从下拉里选。
探针的返回页 focus 到新厂商并渲出它的目录，这个环就断了。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader import crypto
from framework_reader.identity.store import IdentityStore
from framework_reader.llm.config import ModelConfig
from framework_reader.llm.probe import ProbeResult
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

KEY = "sk-live-0123456789abcdef"


def _make(tmp_path, monkeypatch, probe_runner, *, roles=("admin",)):
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
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss", roles=roles)
    app = create_app(db, probe_runner=probe_runner,
                     http_get=lambda url, headers: {"data": [{"id": "deepseek-chat"}]})
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"email": "boss@acme.cn", "password": "pw-boss-boss"})
    return client, ModelConfig(), identity


def _post(client, path, **data):
    page = client.get("/frameworks").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return client.post(path, data={"csrf": found.group(1) if found else "", **data})


def _runner(result, *, seen=None):
    def run(preset, model, api_key):
        if seen is not None:
            seen.append((preset.id, model, api_key))
        return result
    return run


PASSED = ProbeResult(True, "ok", "deepseek / deepseek-chat 通了。",
                     reply="好", elapsed_ms=1234)


@pytest.fixture
def passing(tmp_path, monkeypatch):
    seen = []
    client, config, identity = _make(tmp_path, monkeypatch,
                                     _runner(PASSED, seen=seen))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    return client, config, identity, seen


# ---------- 通了 ----------

def test_a_passing_probe_says_so_and_shows_what_the_model_said(passing):
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-chat").text
    assert "通了" in page
    assert "好" in page


def test_it_probes_what_the_form_has_not_what_the_database_has(passing):
    """表单里选的是 deepseek-reasoner，就该去测它——测 deepseek-chat 等于没测。"""
    client, config, _, seen = passing
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-reasoner")
    assert seen[-1][:2] == ("deepseek", "deepseek-reasoner")
    assert "drafter" not in config.roles()


def test_whitespace_around_the_model_name_is_not_a_different_model(passing):
    """从目录里复制粘贴常带一个尾空格。" deepseek-chat " 和 "deepseek-chat"
    对厂商来说是两个模型名，其中一个不存在。"""
    client, _, _, seen = passing
    _post(client, "/models/role/test", role="drafter", provider="deepseek",
          model="  deepseek-reasoner  ")
    assert seen[-1][1] == "deepseek-reasoner"


def test_probing_never_saves_the_role(passing):
    """「测一下」是问句，不是命令句。测通了也要人自己点保存。"""
    client, config, _, _ = passing
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert "drafter" not in config.roles()


def test_it_uses_the_stored_key_for_that_provider(passing):
    client, _, _, seen = passing
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert seen[-1][2] == KEY


# ---------- 把死路解开 ----------

def test_after_probing_the_new_providers_catalog_is_on_the_page(passing):
    """这是这个按钮的第二个作用：换厂商之后，那家的模型目录要挂得上。"""
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-chat").text
    assert '<datalist id="models-drafter">' in _block(page, "drafter")


# ---------- 不通 ----------

@pytest.mark.parametrize("kind,message", [
    ("auth", "deepseek 拒绝了这把 key。"),
    ("unsupported", "deepseek 不认 no-such 这个模型名。"),
    ("unreachable", "没能连上 deepseek。"),
])
def test_a_failing_probe_shows_the_reason(tmp_path, monkeypatch, kind, message):
    client, _, _ = _make(tmp_path, monkeypatch,
                         _runner(ProbeResult(False, kind, message)))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="no-such").text
    assert message in page


def test_a_failing_probe_does_not_wipe_the_key(tmp_path, monkeypatch):
    """探针失败说明「这组不能用」，不说明「这把 key 该删」。"""
    client, config, _ = _make(
        tmp_path, monkeypatch,
        _runner(ProbeResult(False, "auth", "deepseek 拒绝了这把 key。")))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert config.key("deepseek") == KEY


# ---------- 拒绝出网的那几种情况 ----------

def test_it_refuses_without_a_model_name(passing):
    """不许偷偷回落到 default_model：那样人会以为测的是自己填的那个。"""
    client, _, _, seen = passing
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="").text
    assert "model name" in page
    assert seen == []


def test_it_refuses_when_that_provider_has_no_key(tmp_path, monkeypatch):
    seen = []
    client, _, _ = _make(tmp_path, monkeypatch, _runner(PASSED, seen=seen))
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-chat").text
    assert "has no key yet" in page
    assert seen == []


def test_it_refuses_an_unknown_provider(passing):
    client, _, _, seen = passing
    _post(client, "/models/role/test", role="drafter",
          provider="不存在的厂商", model="x")
    assert seen == []


# ---------- 留痕 ----------

def test_a_probe_is_written_to_the_audit_log(passing):
    """探针是一次真实出网、花组织的钱。§4.4 换端点要留痕，同理。"""
    client, _, identity, _ = passing
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert any(e["event"] == "model.test" for e in identity.audit(20))


def test_what_the_model_replied_never_enters_the_audit_log(tmp_path, monkeypatch):
    """万一有人拿这个框当聊天窗，回来的字不该沉淀进只追加的日志里。"""
    secret = "这句话不该进日志"
    client, _, identity = _make(
        tmp_path, monkeypatch,
        _runner(ProbeResult(True, "ok", "通了。", reply=secret)))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert all(secret not in e["detail"] for e in identity.audit(20))


# ---------- 权限 ----------

def test_an_author_cannot_spend_the_organisations_money(tmp_path, monkeypatch):
    """测一下 = 发一次真实请求。和存 key、换端点同一级门槛（model:write）。

    author 看得见这一页（model:read），但不该能替组织花这笔钱。
    """
    seen = []
    client, _, _ = _make(tmp_path, monkeypatch, _runner(PASSED, seen=seen),
                         roles=("author",))
    assert _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-chat").status_code == 403
    assert seen == []


# ---------- 页面上真的有这个按钮 ----------

def test_the_role_form_offers_a_test_button(passing):
    """路由通了但页面上没有入口，等于没做。"""
    client, _, _, _ = passing
    assert 'formaction="/models/role/test"' in client.get("/models").text


def test_the_model_you_just_probed_is_still_in_the_box(passing):
    """测完要能直接点保存。把刚验过的模型名清掉，等于逼人再填一遍。"""
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-reasoner").text
    assert 'value="deepseek-reasoner"' in page


def test_an_author_sees_no_test_button(tmp_path, monkeypatch):
    client, _, _ = _make(tmp_path, monkeypatch, _runner(PASSED), roles=("author",))
    assert "/models/role/test" not in client.get("/models").text


# ---------- key 从哪儿来，要和起草那条路一致 ----------

def test_it_finds_a_key_that_only_lives_in_the_environment(tmp_path, monkeypatch):
    """`ModelConfig.key_lookup()` 先看库、再回落环境变量，起草走的就是它。

    探针只看库的话，服务器上用环境变量配好的厂商会被报成「还没配 key」——
    而它其实跑得好好的。**一个说「测不了」的探针，比没有探针更让人不敢动。**
    """
    seen = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-the-environment")
    client, _, _ = _make(tmp_path, monkeypatch, _runner(PASSED, seen=seen))
    page = _post(client, "/models/role/test", role="drafter",
                 provider="deepseek", model="deepseek-chat").text
    assert "has no key yet" not in page
    assert seen[-1][2] == "sk-from-the-environment"


def test_the_stored_key_still_wins_over_the_environment(tmp_path, monkeypatch):
    """管理员在页面上填的那把，永远盖过服务器上遗留的那把。"""
    seen = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-the-environment")
    client, _, _ = _make(tmp_path, monkeypatch, _runner(PASSED, seen=seen))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    _post(client, "/models/role/test", role="drafter",
          provider="deepseek", model="deepseek-chat")
    assert seen[-1][2] == KEY


# ---------- 反馈要落在动作发生的地方 ----------

def _block(html: str, role: str) -> str:
    """截出某个角色那一块（`.mrow`）的 HTML。"""
    start = html.index(f"<h3>{role}</h3>")
    nxt = html.find('<div class="mrow">', start)
    return html[start:nxt if nxt != -1 else len(html)]


def test_the_answer_lands_in_the_block_you_clicked_in(passing):
    """按钮在页面中部，反馈只渲在页面顶端——人滚不上去就以为「没反应」。

    这一页已经因为同一个毛病被误读过一次（配 key 那次）。
    反馈要出现在**按下去的那一块里**，不是页面开头。
    """
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="questioner",
                 provider="deepseek", model="deepseek-chat").text
    # 钉的是**那条反馈本身**，不是「通了」两个字——「测通了再点保存」
    # 这句提示语本来就在这一块里，拿它当断言会得到一个永远绿的测试。
    assert '<p class="signed">' in _block(page, "questioner")
    assert "ms" in _block(page, "questioner")


def test_a_failure_lands_there_too(tmp_path, monkeypatch):
    client, _, _ = _make(
        tmp_path, monkeypatch,
        _runner(ProbeResult(False, "auth", "deepseek 拒绝了这把 key。")))
    _post(client, "/models/key", provider="deepseek", key=KEY)
    page = _post(client, "/models/role/test", role="questioner",
                 provider="deepseek", model="deepseek-chat").text
    block = _block(page, "questioner")
    assert '<p class="err">' in block
    assert "拒绝了这把 key" in block


def test_the_same_sentence_is_not_printed_twice(passing):
    """顶上一条、块里一条，两句话一模一样——第一次那个 bug 就是这么来的：
    提交前后画面逐像素一致，因为新出现的那句和早就在那儿的那句长得一样。"""
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="questioner",
                 provider="deepseek", model="deepseek-chat").text
    assert page.count("Not saved yet; save only when it looks right.") == 1


def test_a_message_with_no_block_to_land_in_still_shows_up(passing):
    """角色名都不认识时没有「那一块」可落，这种还是要渲在顶上。"""
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="不存在的角色",
                 provider="deepseek", model="x").text
    assert "No such calling role" in page


def test_the_block_you_acted_in_pulls_the_viewport_to_itself(passing):
    """光把话渲在那一块里还不够——浏览器提交后可能把滚动条打回页面顶端，
    那条字就又跑到视野外了（而我控制不了各家浏览器的滚动恢复行为）。

    `autofocus` 是零 JS 的解法：拿到焦点的元素会被浏览器滚进视野。
    顺带下一步也顺手——测通了就该改模型名或点保存。
    """
    client, _, _, _ = passing
    page = _post(client, "/models/role/test", role="questioner",
                 provider="deepseek", model="deepseek-chat").text
    assert "autofocus" in _block(page, "questioner")
    assert page.count("autofocus") == 1


def test_nothing_grabs_the_focus_on_a_plain_page_load(passing):
    """没人刚动过手的时候，页面不该自己把光标塞进某个框里。"""
    client, _, _, _ = passing
    assert "autofocus" not in client.get("/models").text
