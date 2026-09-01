"""条款页上和 AI 的对话。

三条硬约束：
1. **只在自己导入的框架上开**——内置框架的正文是 Tier C/D 受版权原文，
   一个字不许出网。
2. 每问一句算一次调用，同一本账、同一个预检。
3. **模型的建议要人点头才写库。**
"""
import re
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CID = "ACME-1:3.1"
BUILTIN_CID = "NIST-CSF-2.0:DE.CM-01"


def _make(tmp_path, monkeypatch, chat_runner=None, roles=("author",)):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=BUILTIN_CID, framework_id="NIST-CSF-2.0", label="Networks monitored",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()

    from framework_reader.web.app import create_app

    seen = []

    def counting(control_id, message, history):
        seen.append((control_id, message, history))
        return (chat_runner or (lambda *a: '{"reply":"知道了","updates":[]}'))(
            control_id, message, history)

    client = TestClient(create_app(db, chat_runner=counting),
                        follow_redirects=False)
    client.post("/import",
                data={"framework_id": "ACME-1", "name": "ACME 制度"},
                files={"file": ("f.csv", BytesIO(
                    "编号,标题,正文\n3.1,账号管理,应当为每人分配唯一账号。\n".encode()),
                    "text/csv")})
    env = type("Env", (), {})()
    env.client, env.seen, env.db = client, seen, db
    return env


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _ask(env, text="这条太笼统了", control_id=CID):
    return env.client.post(f"/c/{control_id}/chat", data={"message": text})


# ---------- 只在自己导入的框架上开 ----------

def test_the_box_is_on_an_imported_control(env):
    assert f"/c/{CID}/chat" in env.client.get(f"/c/{CID}").text





# ---------- 一问一答 ----------

def test_what_you_said_shows_up(env):
    _ask(env, "这条的证据该准备什么")
    assert "这条的证据该准备什么" in env.client.get(f"/c/{CID}").text


def test_what_the_ai_said_shows_up(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch,
                lambda *a: '{"reply":"一般准备三样材料","updates":[]}')
    _ask(env)
    assert "一般准备三样材料" in env.client.get(f"/c/{CID}").text


def test_an_empty_message_does_not_cost_a_call(env):
    _ask(env, "   ")
    assert env.seen == []


def test_the_model_sees_the_recent_turns(env):
    """「再具体点」这种追问，没有历史就听不懂。"""
    _ask(env, "第一句")
    _ask(env, "第二句")
    _, _, history = env.seen[-1]
    assert any("第一句" in t.text for t in history)


def test_the_model_does_not_see_more_than_six_turns(env):
    """每一句都要把历史重新喂一遍。不封顶的话聊得越久每句越贵。"""
    for n in range(10):
        _ask(env, f"第 {n} 句")
    _, _, history = env.seen[-1]
    assert len(history) <= 6


# ---------- 建议要人点头 ----------

def _proposes(*a):
    return ('{"reply":"我把「这条在防什么」改了",'
            ' "updates":[{"field":"intent","value":"防的是账号共用追不到人"}]}')


def test_a_proposal_is_not_written_until_you_say_so(tmp_path, monkeypatch):
    """**模型说的话永远不会自己进库。**"""
    from framework_reader.query.api import QueryAPI

    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    fields = QueryAPI(env.db, user_db=None).interpretation(CID)
    assert not (fields.get("intent") or {}).get("value")


def test_the_proposal_shows_up_with_a_button(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    page = env.client.get(f"/c/{CID}").text
    assert "Apply" in page
    assert "/apply" in page


def test_saying_yes_writes_it(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    turn = re.search(r'action="(/c/[^"]+/apply)"',
                     env.client.get(f"/c/{CID}").text).group(1)
    env.client.post(turn)
    page = env.client.get(f"/c/{CID}").text
    assert "防的是账号共用追不到人" in page


def test_what_it_writes_is_marked_as_ai(tmp_path, monkeypatch):
    """谁写的要能看出来。要求是人提的，字是模型写的。"""
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    turn = re.search(r'action="(/c/[^"]+/apply)"',
                     env.client.get(f"/c/{CID}").text).group(1)
    env.client.post(turn)
    assert "AI draft" in env.client.get(f"/c/{CID}").text


def test_saying_yes_twice_only_writes_once(tmp_path, monkeypatch):
    """刷新页面就会重发一次 POST。"""
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    turn = re.search(r'action="(/c/[^"]+/apply)"',
                     env.client.get(f"/c/{CID}").text).group(1)
    env.client.post(turn)
    env.client.post(turn)
    lines = [e for e in IdentityStore().audit(20)
             if e["event"] == "interpretation.chat"]
    assert len(lines) == 1


# ---------- 审计 ----------

def test_applying_a_proposal_is_audited(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env)
    turn = re.search(r'action="(/c/[^"]+/apply)"',
                     env.client.get(f"/c/{CID}").text).group(1)
    env.client.post(turn)
    assert [e for e in IdentityStore().audit(20)
            if e["event"] == "interpretation.chat"]


def test_just_asking_is_not_audited(env):
    """按你定的：只记写库的，单纯问答不记——花的钱另有一本账。"""
    _ask(env, "这条的证据该准备什么")
    assert [e for e in IdentityStore().audit(20)
            if e["event"] == "interpretation.chat"] == []


def test_the_audit_line_keeps_the_conversation_out_of_the_log(tmp_path,
                                                              monkeypatch):
    env = _make(tmp_path, monkeypatch, _proposes)
    _ask(env, "我们用的是内部系统 XYZ-9")
    turn = re.search(r'action="(/c/[^"]+/apply)"',
                     env.client.get(f"/c/{CID}").text).group(1)
    env.client.post(turn)
    detail = next(e["detail"] for e in IdentityStore().audit(20)
                  if e["event"] == "interpretation.chat")
    assert "XYZ-9" not in detail
    assert "防的是账号共用追不到人" not in detail
    assert CID in detail


# ---------- 浮窗用的 JSON 端点 ----------
#
# 选中一段话就地弹个小聊天框，那个框要显示回答，就得 fetch。
# **界限在这儿：JS 只负责「问」和「显示」，每一次写库仍然走普通表单 POST。**
# 写库那条路上挂着预检、审计、和「点头才写」那道闸——让 JS 去写，
# 等于把这三样搬进浏览器。

def _ask_json(env, text="这句怎么理解", quote="", control_id=CID):
    return env.client.post(f"/c/{control_id}/chat.json",
                           data={"message": text, "quote": quote})


def test_the_json_endpoint_answers(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch,
                lambda *a: '{"reply":"这句是说日志不能本地删","updates":[]}')
    got = _ask_json(env).json()
    assert got["reply"] == "这句是说日志不能本地删"


def test_the_quoted_text_reaches_the_model(tmp_path, monkeypatch):
    """选中的那段话是这次提问的上下文，不带上就白选了。"""
    env = _make(tmp_path, monkeypatch)
    _ask_json(env, "这句怎么理解", quote="运维不得本地删除")
    _, message, _ = env.seen[-1]
    assert "运维不得本地删除" in message


def test_the_quote_is_stored_in_the_thread_too(tmp_path, monkeypatch):
    """浮窗里问的和底下那个框里问的，是同一串对话——不能各记各的。"""
    env = _make(tmp_path, monkeypatch)
    _ask_json(env, "这句怎么理解", quote="运维不得本地删除")
    page = env.client.get(f"/c/{CID}").text
    assert "运维不得本地删除" in page


def test_a_proposal_comes_back_with_its_turn_id(tmp_path, monkeypatch):
    """浮窗要能渲出那个「确定，改」的表单，就得知道 turn_id。"""
    env = _make(tmp_path, monkeypatch, _proposes)
    got = _ask_json(env).json()
    assert got["turn_id"]
    assert got["fields"] == ["What it defends against"]


def test_no_proposal_means_no_fields(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch)
    assert _ask_json(env).json()["fields"] == []


def test_the_json_route_writes_nothing_by_itself(tmp_path, monkeypatch):
    """**JS 那条路一个字都不写解读。**"""
    from framework_reader.query.api import QueryAPI

    env = _make(tmp_path, monkeypatch, _proposes)
    _ask_json(env)
    fields = QueryAPI(env.db, user_db=None).interpretation(CID)
    assert not (fields.get("intent") or {}).get("value")


def test_the_json_route_is_refused_on_a_control_that_does_not_exist(tmp_path,
                                                                    monkeypatch):
    env = _make(tmp_path, monkeypatch)
    assert _ask_json(env, control_id="NO-SUCH:9.9").status_code == 404
    assert env.seen == []


def test_the_json_route_costs_a_call_like_any_other(tmp_path, monkeypatch):
    from framework_reader.llm.config import ModelConfig

    env = _make(tmp_path, monkeypatch)
    before = ModelConfig().spent_this_month()
    _ask_json(env)
    assert ModelConfig().spent_this_month() == before + 1


def test_an_empty_message_costs_nothing(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch)
    _ask_json(env, "  ")
    assert env.seen == []


def test_a_model_failure_comes_back_as_a_sentence_not_a_500(tmp_path,
                                                            monkeypatch):
    def boom(*a):
        raise RuntimeError("端点变了")

    env = _make(tmp_path, monkeypatch, boom)
    got = _ask_json(env)
    assert got.status_code == 200
    assert "端点变了" in got.json()["reply"]


# ---------- 选中文字的浮窗 ----------
#
# 这一页原本零 JS。选中文字绕不开 window.getSelection()，所以这里破了
# 那条约束——界限是「JS 只负责问和显示，写库仍然走表单」。

def test_the_popup_is_on_an_imported_control(env):
    page = env.client.get(f"/c/{CID}").text
    assert 'id="pop"' in page
    assert "getSelection" in page


def test_the_popup_is_on_a_builtin_control_too(env):
    page = env.client.get(f"/c/{BUILTIN_CID}").text
    assert 'id="pop"' in page
    assert "getSelection" in page


def test_the_official_mappings_are_the_only_no_go_zone(env):
    """**默认允许，只挡禁区。** 早先的规则是「选区必须整个落在 .chatty 里」，
    那是默认拒绝——从标题拖到正文、跨两个字段、跨段落都不弹，而人本来
    就是那么选的。这一页上真正不能发出去的只有官方映射那一块。"""
    page = env.client.get(f"/c/{CID}").text
    block = re.search(r'<div class="doc noai"><h4>Mappings to other frameworks',
                      page)
    assert block or "Mappings to other frameworks" not in page


def test_nothing_else_is_marked_as_a_no_go_zone(env):
    """禁区多一个，人就多一处选了不弹而不知道为什么。"""
    page = env.client.get(f"/c/{CID}").text
    assert page.count('class="doc noai"') <= 1


def test_the_script_checks_the_zones_not_a_whitelist(env):
    page = env.client.get(f"/c/{CID}").text
    script = _scripts(page)
    assert "noai" in script
    assert "intersectsNode" in script


def _scripts(page: str) -> str:
    """页面上所有内联脚本拼一起。壳里现在有主题防闪的脚本块，
    抓「第一个」已经不再等于「条款页的那个」。"""
    return "".join(re.findall(r"<script>(.*?)</script>", page, re.S))


def test_the_script_pulls_nothing_from_the_network(env):
    """「任何页面都不许引用外部主机」那条守卫还在，这段也不该破它。"""
    page = env.client.get(f"/c/{CID}").text
    script = _scripts(page)
    assert "http://" not in script and "https://" not in script
    assert "<script src" not in page


def test_the_script_never_writes_the_interpretation_itself(env):
    """写库那条路上挂着预检、审计、和「点头才写」那道闸。
    JS 里出现 /apply 只能是拼一个 <form>，不能是 fetch 过去。"""
    page = env.client.get(f"/c/{CID}").text
    fetches = re.findall(r"fetch\(([^)]*)", _scripts(page))
    assert fetches
    assert all("apply" not in f for f in fetches)


# ---------- 两栏：左边读的，右边做的 ----------
#
# 条款可以很长（几十屏）。动作按钮跟着正文滚到看不见的地方，
# 等于每次想动手都要翻回去找。

def test_the_actions_live_in_a_side_column(env):
    page = env.client.get(f"/c/{CID}").text
    assert 'class="split"' in page
    assert 'class="doing"' in page


def test_the_side_column_sticks_while_you_scroll(env):
    page = env.client.get(f"/c/{CID}").text
    assert re.search(r"\.doing \.stuck\{position:sticky", page)


def test_the_reading_column_holds_the_clause_body(env):
    """正文和七个字段留在左边——右栏是「做的事」，不是又一份正文。"""
    page = env.client.get(f"/c/{CID}").text
    left = page[page.index('class="reading"'):page.index('class="doing"')]
    assert "Your imported text" in left


def test_the_chat_is_in_the_side_column(env):
    page = env.client.get(f"/c/{CID}").text
    right = page[page.index('class="doing"'):]
    assert "Ask AI" in right


def test_the_confirm_button_is_in_the_side_column_too(env):
    """按你定的：三块全进右栏。"""
    env.client.post(f"/c/{CID}/edit/intent", data={"value": "防的是账号共用"})
    page = env.client.get(f"/c/{CID}").text
    right = page[page.index('class="doing"'):]
    assert "I confirm this control" in right


def test_the_history_scrolls_inside_the_panel(env):
    """聊久了历史会很长。面板内部滚，输入框始终贴底、始终看得见。"""
    page = env.client.get(f"/c/{CID}").text
    assert 'class="thread"' in page
    assert re.search(r"\.thread\{overflow-y:auto", page)


def test_a_narrow_window_stacks_them_back(env):
    """两栏硬挤在窄屏上，两边都没法看。"""
    page = env.client.get(f"/c/{CID}").text
    assert "@media (max-width:60rem)" in page
    narrow = page[page.index("@media (max-width:60rem)"):]
    assert "position:static" in narrow





def test_the_layout_needs_no_javascript(env):
    """这一页的 JS 只为选区破例过一次。布局用 sticky 就够，不该再欠一笔。"""
    page = env.client.get(f"/c/{CID}").text
    script = _scripts(page)
    for word in ("scroll", "sticky", "getBoundingClientRect().top"):
        assert f"onscroll" not in script
    assert "addEventListener('scroll'" not in script


def test_the_reading_column_is_clearly_wider_than_the_actions(env):
    """左边是要读的东西，右边只是按钮。两栏差不多宽的时候，
    正文被挤成窄条，而按钮占了半个屏幕——那是本末倒置。"""
    page = env.client.get(f"/c/{CID}").text
    css = re.search(r"\.split\{[^}]*grid-template-columns:([^;}]+)", page).group(1)
    assert "1fr" in css
    side = re.search(r"(\d+)rem", css)
    assert side and int(side.group(1)) <= 20


def test_this_page_gets_a_wider_container(env):
    """全站容器是 56rem，两栏挤在里面左边就剩三十几 rem。
    只有这一页放宽——别的页面是单栏长文，宽了反而难读。"""
    page = env.client.get(f"/c/{CID}").text
    assert 'class="wrap wide"' in page


def test_other_pages_keep_the_narrow_container(env):
    """条款页之外都是单栏长文，宽了反而难读。"""
    assert 'class="wrap wide"' not in env.client.get("/frameworks").text
    assert 'class="wrap wide"' not in env.client.get("/import").text


# ---------- 内置框架上也开 ----------
#
# 早先这里一律拦着，理由写的是「受版权原文不得出网」。查下来那个理由不成立：
#   NIST CSF 2.0 / 800-53 是 tier A（美国政府作品，公共领域）
#   ISO 27002 是 tier C，但库里存的是**自写** label（label_is_original=0）
#   original_text 表 0 条——受版权原文根本没进过库
# 出网守卫留着当拦网（哪天真有 C/D 原文进库它会拦），但拿「内置」当判据是错的。

def test_a_builtin_control_has_the_chat_now(env):
    page = env.client.get(f"/c/{BUILTIN_CID}").text
    assert "Ask AI" in page
    assert f"/c/{BUILTIN_CID}/chat" in page


def test_a_builtin_control_can_be_asked(env):
    result = _ask(env, control_id=BUILTIN_CID)
    assert result.status_code in (200, 303)
    assert env.seen


def test_a_builtin_control_can_be_edited(env):
    from framework_reader.query.api import QueryAPI

    env.client.post(f"/c/{BUILTIN_CID}/edit/intent",
                    data={"value": "我给内置条款写的意图"})
    fields = QueryAPI(env.db).interpretation(BUILTIN_CID)
    assert fields["intent"]["value"] == "我给内置条款写的意图"


def test_editing_a_builtin_is_audited_like_any_other(env):
    env.client.post(f"/c/{BUILTIN_CID}/edit/intent", data={"value": "改一句"})
    assert [e for e in IdentityStore().audit(20)
            if e["event"] == "interpretation.edit"]


def test_a_builtin_control_gets_the_two_column_layout(env):
    assert 'class="split"' in env.client.get(f"/c/{BUILTIN_CID}").text


def test_a_builtin_control_has_the_selection_popup(env):
    assert 'id="pop"' in env.client.get(f"/c/{BUILTIN_CID}").text


def test_a_control_that_does_not_exist_is_still_404(env):
    assert env.client.post("/c/NO-SUCH:9.9/chat",
                           data={"message": "在吗"}).status_code == 404
