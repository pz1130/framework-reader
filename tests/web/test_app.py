"""本地 Web 壳。主 spec §7.3.6

只包一层：数据与业务逻辑全在 QueryAPI 与既有模块里，路由不许写裸 SQL（§8①）。
"""
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance, InterpretationState,
)
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CID = "NIST-CSF-2.0:DE.CM-01"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST Cybersecurity Framework 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=CID, framework_id="NIST-CSF-2.0", label="Networks are monitored",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    insert_interpretations(conn, [Interpretation(
        control_id=CID, state=InterpretationState.DRAFT,
        fields={
            name: Field(
                value={"1": "有探针", "2": "有清单", "3": "自动化"} if name == "practice"
                else ("防的是没人看网络" if name == "intent" else "x"),
                basis=Basis.INFERRED)
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance())])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db))


def test_home_lists_the_builtin_framework(client):
    page = client.get("/frameworks").text
    assert "NIST Cybersecurity Framework 2.0" in page


def test_home_shows_how_many_controls_have_an_interpretation(client):
    """没解读的框架点进去是空目录。覆盖率要在主页就看得见。"""
    assert "1/1" in client.get("/frameworks").text


def test_the_frameworks_page_does_not_double_as_an_import_form(client):
    """框架页是目录。导入表单在 /import 那一页，顶栏有链接指过去——
    不在框架页里再叠一份。"""
    page = client.get("/frameworks").text
    assert 'action="/import"' not in page
    assert 'type="file"' not in page


def test_a_framework_page_lists_its_controls(client):
    page = client.get("/f/NIST-CSF-2.0").text
    assert "DE.CM-01" in page


def test_a_framework_page_links_back_to_the_catalog(client):
    """条款页能回框架，框架页也得能回目录——层级里不许有回不去的一层。"""
    page = client.get("/f/NIST-CSF-2.0").text
    assert '<a class="back" href="/frameworks">' in page
    assert "← Back to Frameworks" in page


def test_an_unknown_framework_is_404(client):
    assert client.get("/f/NOPE").status_code == 404


def test_a_control_page_renders_the_interpretation(client):
    page = client.get(f"/c/{CID}").text
    assert "防的是没人看网络" in page
    assert "What it defends against" in page


def test_a_draft_control_page_says_it_is_a_draft(client):
    assert "AI draft" in client.get(f"/c/{CID}").text


def test_an_unknown_control_is_404(client):
    assert client.get("/c/NIST-CSF-2.0:NOPE").status_code == 404


# ---------- 导入 ----------

def _upload(client, body: bytes, name="f.csv", **form):
    data = {"framework_id": "ACME-1", "name": "ACME 制度"}
    data.update(form)
    return client.post(
        "/import", data=data, files={"file": (name, BytesIO(body), "text/csv")},
        follow_redirects=False,
    )


def test_importing_a_csv_lands_a_new_framework(client):
    body = "编号,标题\n3.1,账号管理\n".encode()
    assert _upload(client, body).status_code in (302, 303)
    # 导入的框架不再堆在主页卡片区，收进「我导入的」那一页。
    assert "ACME 制度" in client.get("/mine").text


def test_an_imported_framework_is_marked_as_imported(client):
    _upload(client, "编号,标题\n3.1,账号管理\n".encode())
    assert "Import framework" in client.get("/frameworks").text


def test_a_bad_table_shows_the_row_number_instead_of_a_stack_trace(client):
    body = "编号,标题\n,没有编号\n".encode()
    result = _upload(client, body)
    assert result.status_code == 200
    assert "Row 2" in result.text


def test_an_unsupported_file_type_is_explained(client):
    result = _upload(client, b"x", name="f.docx")
    assert "csv" in result.text


def test_an_oversized_framework_upload_is_rejected(client, monkeypatch):
    from framework_reader.web import uploads

    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 8)
    result = _upload(client, b"number,title\n1,Account management\n")
    assert result.status_code == 413
    assert "over" in result.text


def test_the_import_needs_an_id_and_a_name(client):
    body = "编号,标题\n3.1,账号\n".encode()
    assert _upload(client, body, framework_id="").status_code == 200


# ---------- 契约 ----------

def _sqlite_imports(source: str) -> list[str]:
    """源码里有没有**直接拿 sqlite3 干活**。

    看 AST 而不是找字符串：`media_type="application/vnd.sqlite3"` 是一个
    MIME 类型，不是一次数据库调用。找字符串的版本会被它绊倒，而绊倒之后
    人只会把断言删掉——那就等于这条契约没了。
    """
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name.split(".")[0] == "sqlite3"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "sqlite3":
                found.append(node.module)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "sqlite3":
                found.append(f"sqlite3.{node.attr}")
    return found


def test_the_raw_sql_detector_still_bites():
    """探测器自己也要有测试。

    否则「把断言修准了」和「把断言放宽到永远为真」，从外面看长得一模一样。
    """
    assert _sqlite_imports("import sqlite3\n") == ["sqlite3"]
    assert _sqlite_imports("from sqlite3 import Row\n") == ["sqlite3"]
    assert _sqlite_imports("conn = sqlite3.connect(p)\n") == ["sqlite3.connect"]
    assert _sqlite_imports('m = "application/vnd.sqlite3"\n') == []


def test_the_web_layer_writes_no_raw_sql(client):
    """§8①：查询一律走 QueryAPI，Web 层不许写裸 SQL。"""
    import inspect

    from framework_reader.web import app as module

    source = inspect.getsource(module)
    assert _sqlite_imports(source) == []
    for word in ("SELECT ", "INSERT "):
        assert word not in source


# ---------- 网页上自评 ----------

def test_the_framework_page_links_to_assessment(client):
    page = client.get("/f/NIST-CSF-2.0").text
    assert "/f/NIST-CSF-2.0/assess" in page
    assert "/f/NIST-CSF-2.0/gap" in page


def test_a_framework_with_rungs_asks_for_a_level(client):
    page = client.get("/f/NIST-CSF-2.0/assess").text
    assert "有探针" in page and "自动化" in page
    assert "What level" in page


def test_saving_a_level_shows_up_in_the_gap_report(client):
    client.post("/f/NIST-CSF-2.0/assess",
                data={"control_id": CID, "answer": "1", "note": "只有边界有探针"},
                follow_redirects=False)
    gap = client.get("/f/NIST-CSF-2.0/gap").text
    assert "只有边界有探针" in gap
    assert "有清单" in gap          # 下一档的原话


def test_saving_redirects_back_to_the_assessment_page(client):
    result = client.post("/f/NIST-CSF-2.0/assess",
                         data={"control_id": CID, "answer": "0", "note": ""},
                         follow_redirects=False)
    assert result.status_code == 303
    assert "/f/NIST-CSF-2.0/assess" in result.headers["location"]


def test_an_already_answered_control_shows_its_current_answer(client):
    client.post("/f/NIST-CSF-2.0/assess",
                data={"control_id": CID, "answer": "2", "note": "有清单了"})
    page = client.get("/f/NIST-CSF-2.0/assess").text
    assert "有清单了" in page


def test_not_applicable_is_recorded_with_its_reason(client):
    client.post("/f/NIST-CSF-2.0/assess",
                data={"control_id": CID, "answer": "n", "note": "无工控网络"})
    from framework_reader.assess.store import AssessStore

    got = AssessStore().get(CID)
    assert got.applicable is False and got.reason == "无工控网络"


def test_a_framework_without_rungs_asks_soa_questions(client):
    """导入的框架还没起草解读——问不出「几档」，该问适用性。"""
    client.post("/import", data={"framework_id": "ACME-1", "name": "ACME"},
                files={"file": ("f.csv", BytesIO("编号,标题\n3.1,账号\n".encode()), "text/csv")})
    page = client.get("/f/ACME-1/assess").text
    assert "Implementation status" in page
    assert "What level" not in page


def test_the_soa_page_lists_every_control_including_unfilled(client):
    page = client.get("/f/NIST-CSF-2.0/soa").text
    assert "DE.CM-01" in page and "TBD" in page


def test_the_soa_downloads_as_csv(client):
    result = client.get("/f/NIST-CSF-2.0/soa.csv")
    assert result.status_code == 200
    assert result.text.lstrip("\ufeff").startswith("Control,")
    assert "attachment" in result.headers["content-disposition"]


def test_the_gap_report_of_an_unassessed_framework_says_so(client):
    assert "No self-assessment yet" in client.get("/f/NIST-CSF-2.0/gap").text


def test_an_unknown_path_returns_a_readable_page_not_json(client):
    """默认的 {"detail":"Not Found"} 看起来就是「点了没东西」，还没得滚。"""
    result = client.get("/f/NIST-CSF-2.0/nope")
    assert result.status_code == 404
    assert "Framework Workbench" in result.text
    assert "detail" not in result.text


def test_the_gap_block_can_scroll_sideways_on_a_long_line():
    from framework_reader.web import views

    assert "overflow-x:auto" in views._ASSESS_CSS


def test_serve_offers_reload_so_a_stale_process_stops_biting():
    import inspect

    from framework_reader.cli.main import serve

    assert "reload" in inspect.signature(serve).parameters


# ---------- 导入的框架：正文与起草 ----------

def _import_with_body(client):
    body = "编号,标题,正文\n4.1,日志留存,日志集中采集，留存不少于六个月，运维不得本地删除。\n"
    return _upload(client, body.encode())   # _upload 的默认编号就是 ACME-1


def test_an_imported_control_shows_the_body_you_uploaded(client):
    """你上传的正文躺在库里，页面却说「还没有解读」——等于把你自己的字扣下了。"""
    _import_with_body(client)
    page = client.get("/c/ACME-1:4.1").text
    assert "留存不少于六个月" in page


def test_the_body_is_labelled_as_yours_not_as_ours(client):
    _import_with_body(client)
    assert "Your imported text" in client.get("/c/ACME-1:4.1").text


def test_a_builtin_control_shows_no_body_block(client):
    """CSF 的正文由官方 label 兑现——块有，但标「官方原文」，
    不该标成用户导入的（内容包里的 original_text 仍然是空的）。"""
    page = client.get(f"/c/{CID}").text
    assert "Networks are monitored" in page
    assert "Official text" in page
    assert "Your imported text" not in page
    assert "paste in a passage" not in page


def test_an_imported_framework_offers_to_draft_on_the_page(client):
    _import_with_body(client)
    assert 'action="/f/ACME-1/draft"' in client.get("/f/ACME-1").text


def test_a_builtin_framework_does_not_offer_to_draft(client):
    """内置框架的解读是我们的内容，由 fr draft 起草并评审，不在用户的按钮上。"""
    assert "/draft" not in client.get("/f/NIST-CSF-2.0").text


def test_the_draft_button_says_how_many_controls_it_will_bill_for(client):
    """一次点击就是一次花钱。花在几条上，点之前必须看得见。"""
    _import_with_body(client)
    assert "Draft these 1 controls" in client.get("/f/ACME-1").text


def test_an_undrafted_imported_control_points_at_the_button_not_the_cli(client):
    _import_with_body(client)
    assert "Draft interpretations" in client.get("/c/ACME-1:4.1").text


# ---------- 网页上起草 ----------

@pytest.fixture
def drafting(tmp_path, monkeypatch):
    """起草一律走替身：测试不出网（tests/test_no_network_in_tests.py）。"""
    import sqlite3 as _sqlite3

    from framework_reader.interpret.batch import DraftFailure, DraftReport
    from framework_reader.pack.db import (
        create_schema, insert_controls, insert_frameworks,
    )
    from framework_reader.schema.entities import (
        Framework, FrameworkControl, LicenseTier,
    )
    from framework_reader.web import jobs

    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    jobs.reset()
    db = tmp_path / "content.sqlite"
    conn = _sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()

    calls: list[str] = []
    overlay_dbs: list = []
    outcome: dict = {"report": DraftReport(written=["ACME-1:4.1"]), "raise": None}

    def runner(framework_id: str, user_db=None, only=None):
        calls.append(framework_id)
        overlay_dbs.append(user_db)
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return outcome["report"]

    from framework_reader.web.app import create_app

    client = TestClient(create_app(db, draft_runner=runner))
    return type("Fixture", (), {
        "client": client, "calls": calls, "outcome": outcome,
        "overlay_dbs": overlay_dbs,
        "DraftReport": DraftReport, "DraftFailure": DraftFailure,
    })()


def _wait_for_draft(client, key="ACME-1", tries=200):
    """任务在后台线程里跑。轮询到不再是「起草中」为止，跟用户看到的一样。"""
    import time

    path = f"/c/{key}/draft" if ":" in key else f"/f/{key}/draft"
    for _ in range(tries):
        page = client.get(path).text
        if "Drafting" not in page:
            return page
        time.sleep(0.01)
    raise AssertionError("起草一直没结束")


def test_starting_a_draft_calls_the_drafter_once(drafting):
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    _wait_for_draft(drafting.client)
    assert drafting.calls == ["ACME-1"]


def test_a_finished_draft_reports_how_many_it_wrote(drafting):
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    assert "Drafted 1 controls" in _wait_for_draft(drafting.client)


def test_a_finished_draft_still_calls_it_a_draft(drafting):
    """网页起草不等于确认。这行字没了，用户会把初稿当定稿交出去。"""
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    assert "AI draft" in _wait_for_draft(drafting.client)


def test_a_missing_api_key_is_explained_not_swallowed(drafting):
    from framework_reader.llm.registry import MissingApiKeyError

    drafting.outcome["raise"] = MissingApiKeyError("环境变量 DEEPSEEK_API_KEY 没设")
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    assert "DEEPSEEK_API_KEY" in _wait_for_draft(drafting.client)


def test_a_control_that_failed_is_listed_by_name(drafting):
    """106 条里坏一条不能只说「成功 105」——哪一条坏的必须点得出来。"""
    drafting.outcome["report"] = drafting.DraftReport(
        written=[], failed=[drafting.DraftFailure(control_id="ACME-1:4.1", reason="模型超时")]
    )
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    page = _wait_for_draft(drafting.client)
    assert "4.1" in page and "模型超时" in page


def test_a_builtin_framework_is_drafted_as_an_overlay(drafting, tmp_path):
    """内置框架也能从网页起草：overlay 进用户库当工作副本，不进 git。
    要写内容包（发布用）仍走 `fr draft`。"""
    result = drafting.client.post("/f/NIST-CSF-2.0/draft", follow_redirects=False)
    assert result.status_code == 303
    _wait_for_draft(drafting.client, key="NIST-CSF-2.0")
    assert drafting.calls == ["NIST-CSF-2.0"]
    assert drafting.overlay_dbs[-1] == tmp_path / "home" / "user.sqlite"


def test_refreshing_does_not_start_a_second_paid_run(drafting):
    """刷新进度页不该再点一次钱。"""
    _import_with_body(drafting.client)
    drafting.client.post("/f/ACME-1/draft", follow_redirects=False)
    _wait_for_draft(drafting.client)
    drafting.client.get("/f/ACME-1/draft")
    assert drafting.calls == ["ACME-1"]


def test_the_progress_page_refreshes_itself_while_running(drafting):
    """跑几分钟的活儿，页面不会自己动的话用户只能盯着猜。"""
    import threading

    gate = threading.Event()
    drafting.outcome["report"] = drafting.DraftReport(written=[])
    original = drafting.calls

    def slow(framework_id: str, user_db=None):
        original.append(framework_id)
        gate.wait(5)
        return drafting.DraftReport(written=[])

    from framework_reader.web import jobs
    from framework_reader.interpret.run import pending_controls  # noqa: F401

    _import_with_body(drafting.client)
    jobs.start("ACME-1", 1, slow)
    try:
        page = drafting.client.get("/f/ACME-1/draft").text
        assert 'http-equiv="refresh"' in page and "Drafting" in page
    finally:
        gate.set()


def test_the_status_page_of_a_framework_never_drafted_goes_back(drafting):
    _import_with_body(drafting.client)
    result = drafting.client.get("/f/ACME-1/draft", follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == "/f/ACME-1"


# ---------- 导入入口的可见性 ----------

def test_every_page_has_a_way_to_reach_the_import(client):
    """导入只挂在主页最底下，停在条款页的人整个界面里找不到它。"""
    for path in ("/", "/f/NIST-CSF-2.0", f"/c/{CID}"):
        assert 'href="/import"' in client.get(path).text, f"{path} 上没有导入入口"


def test_the_import_page_stands_on_its_own(client):
    page = client.get("/import").text
    assert 'action="/import"' in page and 'type="file"' in page


def test_the_import_page_is_reachable_after_a_failed_upload(client):
    """报错要报在那一页上，不能把人踢回主页去重新找。"""
    result = _upload(client, "编号,标题\n,没有编号\n".encode())
    assert result.status_code == 200 and "Row 2" in result.text


# ---------- 换框架走首页 ----------

def test_no_page_renders_the_framework_switcher(client):
    """顶上摊一排方框，框架一多就折行。换框架点左上角回首页。"""
    _import_with_body(client)
    pages = (
        "/", "/import", "/f/NIST-CSF-2.0", f"/c/{CID}",
        "/f/NIST-CSF-2.0/assess", "/f/NIST-CSF-2.0/gap", "/f/NIST-CSF-2.0/soa",
        "/search?q=DE.CM-01",
    )
    for path in pages:
        assert '<div class="tabs">' not in client.get(path).text, path


def test_every_page_can_get_home(client):
    pages = (
        "/", "/import", "/f/NIST-CSF-2.0", f"/c/{CID}",
        "/f/NIST-CSF-2.0/assess", "/f/NIST-CSF-2.0/gap", "/f/NIST-CSF-2.0/soa",
        "/search?q=DE.CM-01",
    )
    for path in pages:
        assert '<h1><a href="/">Framework Workbench</a></h1>' in client.get(path).text, path


def test_a_control_page_still_names_its_framework(client):
    """顶栏不再列框架之后，条款页得自己说出你在哪儿。"""
    page = client.get(f"/c/{CID}").text
    assert "Back to NIST Cybersecurity Framework 2.0" in page


# ---------- 改自己的解读 ----------

def _drafted_import(client):
    """导入一个框架，并给它一条模型起草的解读。"""
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation,
    )
    from framework_reader.interpret.user_store import UserInterpretationStore

    _import_with_body(client)
    UserInterpretationStore().save(Interpretation(
        control_id="ACME-1:4.1",
        fields={
            name: Field(
                value=({"1": "一档", "2": "二档", "3": "三档"} if name == "practice"
                       else ["模型想到的追问"] if name == "auditor_asks"
                       else "模型写的"),
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
    ))


def test_each_field_of_your_own_control_can_be_edited(client):
    _drafted_import(client)
    assert 'href="/c/ACME-1:4.1/edit/intent"' in client.get("/c/ACME-1:4.1").text


def test_a_builtin_control_can_be_edited_too(client):
    """早先这里拦着，理由写的是「受版权原文不得出网」。查下来不成立：
    CSF/800-53 是公共领域，ISO 的 label 是自写的，original_text 表 0 条。
    而团队九成时间在用 CSF 和 800-53——拦着它们等于这个功能白做。

    改动逐字段盖住内容包那一版，见 query/api.py 的合并视图。"""
    assert "/edit/" in client.get(f"/c/{CID}").text


def test_the_edit_form_starts_from_what_is_there_now(client):
    _drafted_import(client)
    assert "模型写的" in client.get("/c/ACME-1:4.1/edit/intent").text


def test_saving_an_edit_shows_the_new_text(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "我自己的说法"},
                follow_redirects=False)
    assert "我自己的说法" in client.get("/c/ACME-1:4.1").text


def test_an_edited_field_is_marked_as_yours(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "我自己的说法"},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "You wrote this" in page


def test_an_untouched_field_still_says_it_is_ai(client):
    """整条一句「AI 初稿」看不出你改过哪几句。标记必须逐字段。"""
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "我自己的说法"},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "AI draft" in page and "You wrote this" in page


def test_the_three_rungs_are_edited_as_three_boxes(client):
    _drafted_import(client)
    form = client.get("/c/ACME-1:4.1/edit/practice").text
    assert form.count("<textarea") == 3


def test_saving_the_three_rungs_keeps_them_three(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/practice",
                data={"v1": "先做这个", "v2": "再做这个", "v3": "最后"},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "先做这个" in page and "再做这个" in page and "最后" in page


def test_a_list_field_is_edited_one_per_line(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/auditor_asks",
                data={"value": "第一问\n第二问"}, follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "第一问" in page and "第二问" in page


def test_writing_a_field_on_an_undrafted_control_works(client):
    """不想让模型碰的条款，用户应当能直接自己写。"""
    _import_with_body(client)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "我自己写的"},
                follow_redirects=False)
    assert "我自己写的" in client.get("/c/ACME-1:4.1").text


def test_an_unknown_field_is_a_readable_page_not_a_stack_trace(client):
    _drafted_import(client)
    assert client.get("/c/ACME-1:4.1/edit/nope").status_code == 404


def test_editing_a_builtin_control_lands(client):
    from framework_reader.query.api import QueryAPI

    client.post(f"/c/{CID}/edit/intent", data={"value": "我给内置条款写的"},
                follow_redirects=False)
    assert "我给内置条款写的" in client.get(f"/c/{CID}").text


def test_a_control_that_does_not_exist_is_still_404(client):
    """闸放开的是「内置框架」，不是「什么都收」。"""
    result = client.post("/c/NO-SUCH:9.9/edit/intent", data={"value": "x"},
                         follow_redirects=False)
    assert result.status_code == 404


# ---------- 认领这条 ----------

def test_your_own_control_can_be_confirmed(client):
    _drafted_import(client)
    assert 'action="/c/ACME-1:4.1/confirm"' in client.get("/c/ACME-1:4.1").text


def test_confirming_says_who_signed_it(client):
    import getpass

    _drafted_import(client)
    client.post("/c/ACME-1:4.1/confirm", follow_redirects=False)
    assert getpass.getuser() in client.get("/c/ACME-1:4.1").text


def test_a_confirmed_control_stops_calling_itself_a_draft(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/confirm", follow_redirects=False)
    assert "not yet confirmed" not in client.get("/c/ACME-1:4.1").text


def test_editing_after_confirming_says_the_signature_is_void(client):
    """签完又改，页面必须把签名作废这件事说出来，而不是继续显示已确认。"""
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/confirm", follow_redirects=False)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "又改了"},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "Confirmed" not in page


def test_a_builtin_control_cannot_be_confirmed(client):
    result = client.post(f"/c/{CID}/confirm", follow_redirects=False)
    assert result.status_code == 400


def test_confirming_something_with_no_interpretation_is_refused(client):
    _import_with_body(client)
    result = client.post("/c/ACME-1:4.1/confirm", follow_redirects=False)
    assert result.status_code in (400, 404)


def test_the_framework_page_shows_which_controls_you_have_claimed(client):
    """哪几条已经有人认领，是这个框架能不能交出去的唯一指标。"""
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/confirm", follow_redirects=False)
    assert "Confirmed" in client.get("/f/ACME-1").text


def test_a_control_you_wrote_part_of_does_not_call_itself_all_ai(client):
    """七个字段里有你写的，顶上还挂「AI 初稿」——标注往反方向撒谎了。"""
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/intent", data={"value": "我自己的说法"},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    banner = page[page.index("<h2>"):page.index("Your imported text")]
    assert "AI draft" not in banner
    assert "Unconfirmed" in banner


def test_a_control_nobody_touched_still_says_it_is_all_ai(client):
    _drafted_import(client)
    page = client.get("/c/ACME-1:4.1").text
    assert "AI draft" in page[page.index("<h2>"):page.index("Your imported text")]


# ---------- 单条补空缺 ----------

def test_a_control_with_blanks_offers_to_fill_them(client):
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/regional_note", data={"value": ""},
                follow_redirects=False)
    assert 'action="/c/ACME-1:4.1/draft"' in client.get("/c/ACME-1:4.1").text


def test_an_undrafted_control_offers_to_draft_itself(client):
    """整框架起草要为几十条付钱。只想试一条的人得有单条的入口。"""
    _import_with_body(client)
    assert 'action="/c/ACME-1:4.1/draft"' in client.get("/c/ACME-1:4.1").text


def test_a_builtin_control_offers_the_draft_button_too(client):
    assert 'action="/c/' in client.get(f"/c/{CID}").text


def test_filling_one_control_does_not_draft_the_whole_framework(drafting):
    _import_with_body(drafting.client)
    drafting.client.post("/c/ACME-1:4.1/draft", follow_redirects=False)
    _wait_for_draft(drafting.client, "ACME-1:4.1")
    assert drafting.calls == ["ACME-1:4.1"]


def test_the_single_control_progress_page_reports_the_result(drafting):
    _import_with_body(drafting.client)
    drafting.client.post("/c/ACME-1:4.1/draft", follow_redirects=False)
    assert "Drafted 1 controls" in _wait_for_draft(drafting.client, "ACME-1:4.1")


# ---------- 让 AI 重写 ----------

def test_each_field_offers_a_rewrite(client):
    _drafted_import(client)
    assert 'href="/c/ACME-1:4.1/rewrite/intent"' in client.get("/c/ACME-1:4.1").text


def test_an_empty_field_offers_no_rewrite(client):
    """空字段没有可改写的东西，那是「写」不是「重写」。"""
    _drafted_import(client)
    client.post("/c/ACME-1:4.1/edit/regional_note", data={"value": ""},
                follow_redirects=False)
    page = client.get("/c/ACME-1:4.1").text
    assert "/rewrite/regional_note" not in page


def test_the_rewrite_form_shows_what_it_will_rewrite(client):
    _drafted_import(client)
    assert "模型写的" in client.get("/c/ACME-1:4.1/rewrite/intent").text


def test_rewriting_replaces_the_field(rewriting):
    _drafted_import(rewriting.client)
    rewriting.client.post("/c/ACME-1:4.1/rewrite/intent",
                          data={"instruction": "带上系统名"}, follow_redirects=False)
    assert "重写后的说法" in rewriting.client.get("/c/ACME-1:4.1").text


def test_your_instruction_is_passed_through(rewriting):
    _drafted_import(rewriting.client)
    rewriting.client.post("/c/ACME-1:4.1/rewrite/intent",
                          data={"instruction": "带上系统名"}, follow_redirects=False)
    assert rewriting.calls == [("ACME-1:4.1", "intent", "带上系统名")]


def test_a_rewritten_field_is_still_marked_as_ai(rewriting):
    """要求是你提的，字是模型写的。记成「你写的」等于替你认领没写过的话。"""
    _drafted_import(rewriting.client)
    rewriting.client.post("/c/ACME-1:4.1/rewrite/intent",
                          data={"instruction": "带上系统名"}, follow_redirects=False)
    page = rewriting.client.get("/c/ACME-1:4.1").text
    block = page[page.index("What it defends against"):page.index("Plain words")]
    assert "AI draft" in block and "You wrote this" not in block


def test_an_empty_instruction_is_refused_before_spending(rewriting):
    _drafted_import(rewriting.client)
    result = rewriting.client.post("/c/ACME-1:4.1/rewrite/intent",
                                   data={"instruction": "   "}, follow_redirects=False)
    assert result.status_code == 200 and rewriting.calls == []


def test_a_model_that_returns_junk_says_so_instead_of_saving_it(rewriting):
    from framework_reader.interpret.drafter import DrafterOutputError

    rewriting.outcome["raise"] = DrafterOutputError("practice 必须是三档字典")
    _drafted_import(rewriting.client)
    result = rewriting.client.post("/c/ACME-1:4.1/rewrite/practice",
                                   data={"instruction": "换个说法"},
                                   follow_redirects=False)
    assert "三档" in result.text
    assert "模型写的" in rewriting.client.get("/c/ACME-1:4.1").text


def test_a_builtin_control_can_be_rewritten_too(rewriting):
    rewriting.client.post(f"/c/{CID}/rewrite/intent",
                          data={"instruction": "再具体点"}, follow_redirects=False)
    assert rewriting.calls



@pytest.fixture
def rewriting(tmp_path, monkeypatch):
    """重写一律走替身：测试不出网。"""
    import sqlite3 as _sqlite3

    from framework_reader.pack.db import (
        create_schema, insert_controls, insert_frameworks,
    )
    from framework_reader.schema.entities import (
        Framework, FrameworkControl, LicenseTier,
    )
    from framework_reader.web import jobs

    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    jobs.reset()
    db = tmp_path / "content.sqlite"
    conn = _sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=CID, framework_id="NIST-CSF-2.0", label="Networks are monitored",
        label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()

    calls: list[tuple[str, str, str]] = []
    outcome: dict = {"value": "重写后的说法", "raise": None}

    def runner(control_id: str, field: str, instruction: str):
        calls.append((control_id, field, instruction))
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return outcome["value"]

    from framework_reader.web.app import create_app

    client = TestClient(create_app(db, rewrite_runner=runner))
    return type("Fixture", (), {
        "client": client, "calls": calls, "outcome": outcome,
    })()


# ---------- 进得去，也得出得来 ----------
#
# 换框架走首页。条款页还得能回到**本框架**：改字段页和重写页都有「不改了」
# 回条款页，唯独条款页自己没有任何回到框架的入口。

def test_a_control_page_offers_a_way_back_to_its_framework(client):
    """钉那个返回链接本身——光找 href="/f/…" 会误把别的入口算进来。"""
    page = client.get(f"/c/{CID}").text
    assert '<a class="back" href="/f/NIST-CSF-2.0">' in page


def test_the_way_back_is_labelled_with_the_framework_name(client):
    """标编号等于把顶栏那个面包屑再印一遍。名字才说明你要回哪儿。"""
    page = client.get(f"/c/{CID}").text
    assert "Back to NIST Cybersecurity Framework 2.0" in page


def test_the_breadcrumb_is_a_link_on_a_control_page(client):
    """`.crumb a` 这条 CSS 规则一直写着，全站却一次都没用上——
    面包屑长得像能点，点了没反应。"""
    import re

    page = client.get(f"/c/{CID}").text
    crumb = re.search(r'<span class="crumb">(.*?)</span>', page).group(1)
    assert 'href="/f/NIST-CSF-2.0"' in crumb


def test_the_breadcrumb_is_a_link_on_the_edit_and_rewrite_pages(client):
    """这两页能回条款页，但回不到框架——只是把死路往里挪了一层。

    只有自己导入的框架能进这两页（内置框架的解读不在用户的按钮上改），
    所以这里得先导一个进来。
    """
    import re

    _upload(client, "编号,标题\n3.1,账号管理\n".encode())
    cid = "ACME-1:3.1"
    for path in (f"/c/{cid}/edit/intent", f"/c/{cid}/rewrite/intent"):
        page = client.get(path).text
        crumb = re.search(r'<span class="crumb">(.*?)</span>', page).group(1)
        assert 'href="/f/ACME-1"' in crumb, path


def test_a_breadcrumb_with_nowhere_to_go_stays_plain_text(client):
    """导入页的面包屑是「导入」，它不对应任何框架。给它一个 href 只能编一个出来。"""
    import re

    page = client.get("/import").text
    crumb = re.search(r'<span class="crumb">(.*?)</span>', page).group(1)
    assert "<a" not in crumb


# ---------- 差距报告的空态不该把人赶去开终端 ----------

def test_an_empty_gap_report_points_at_the_web_assessment(client):
    """`render_gap` 的空态写的是「先跑 fr assess」。那是给 CLI 用户的正确答案，
    渲到网页上就成了一句把人赶去开终端的指令——而正确答案是上面那个标签。

    部署形态已经是「一个组织多个用户按角色分权」，那些人没有终端。
    """
    # 钉那个行动按钮本身——上面的子导航里本来就有一个同样的 href，
    # 光找 href 会得到一个一直都绿的测试。
    page = client.get("/f/NIST-CSF-2.0/gap").text
    assert '<a class="cta" href="/f/NIST-CSF-2.0/assess">' in page


def test_an_empty_gap_report_does_not_name_a_command_line_tool(client):
    page = client.get("/f/NIST-CSF-2.0/gap").text
    assert "fr assess" not in page


def test_an_empty_gap_report_says_why_it_is_empty(client):
    """「这儿什么都没有」和「这儿什么都没有，因为你还没做那一步」差一整个动作。

    钉的是解释那句，不是「还没有自评」——那四个字 CLI 的空态里也有。
    """
    page = client.get("/f/NIST-CSF-2.0/gap").text
    assert "The gap report comes from the self-assessment" in page


def test_a_gap_report_with_data_is_untouched(client):
    """空态换了，有数据那条路一个字都不该变。"""
    client.post("/f/NIST-CSF-2.0/assess",
                data={"control_id": CID, "answer": "1", "note": "只有边界有探针"},
                follow_redirects=False)
    page = client.get("/f/NIST-CSF-2.0/gap").text
    assert "只有边界有探针" in page
    assert "有清单" in page                    # 下一档的原话
    assert '<a class="cta"' not in page        # 有数据了就不该再劝人去自评


def test_the_command_line_still_says_run_fr_assess():
    """CLI 那句话不动——在终端里 `fr assess` 就是对的答案。"""
    from framework_reader.assess.report import build_gap, render_gap

    assert "fr assess" in render_gap(build_gap([], {}, total=0))


# ---------- 导入框收得下用户手里的文件 ----------

def test_the_import_box_accepts_word_and_pdf(client):
    """`accept=".csv,.xlsx,.xlsm"` 是「导入按钮无法使用」的真正成因：
    文件选择器把用户手里的 Word / PDF 全灰掉，一个都选不中。"""
    page = client.get("/import").text
    assert ".docx" in page and ".pdf" in page


def test_the_import_box_says_the_two_paths_end_differently(client):
    """表格直接落库、文档要先切分再确认——结果不一样，得说。"""
    page = client.get("/import").text
    assert "confirm" in page


def test_the_import_box_says_scans_are_not_taken_yet(client):
    """一期不收扫描件。不写清楚，用户会传一份扫描 PDF 然后撞一堵墙。"""
    assert "Scanned" in client.get("/import").text


# ---------- 页面不许依赖任何外部主机 ----------
#
# 这个产品给中国的安全团队用，部署在他们自己的服务器上。
# `<link rel=stylesheet>` 是**渲染阻塞**的——外部主机连不上时，
# 浏览器会等到超时才继续，那段时间里页面是半死的。
# 而 fonts.googleapis.com 在墙内连不上。
#
# 中文正文本来就用系统字体（PingFang SC / 微软雅黑 / 思源黑体），
# 西文那两款也各有回落链。去掉外链只损失一点西文观感。

def test_no_page_reaches_out_to_an_external_host(client):
    import re

    for path in ("/", "/import", f"/f/NIST-CSF-2.0", f"/c/{CID}"):
        html = client.get(path).text
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert external == [], f"{path} 引用了外部主机：{external}"


def test_the_login_page_does_not_either(client):
    """登录页最要紧——连不上外部主机时，人连门都进不来。"""
    import re

    html = client.get("/login").text
    assert re.findall(r'(?:src|href)="(https?://[^"]+)"', html) == []


def test_the_file_picker_does_not_grey_anything_out(client):
    """`accept` 的唯一好处是方便，代价是**文件在那儿但点不中，而且没有任何解释**。

    实测（2026-08-25）：对话框弹出来，用户的文件是灰的，看起来就是「点了没反应」。
    .doc 改名成 .docx、系统 UTI 认不出来、从别处拷来的文件丢了扩展名——
    每一种都会掉进这个坑，而它们在服务端本来就有确切的报错。

    宁可让人选中一个我们不收的文件，然后告诉他为什么不收。
    """
    page = client.get("/import").text
    assert "accept=" not in page


def test_the_supported_formats_are_still_stated(client):
    """去掉 accept 不等于不说。格式写在提示语里，人看得见。"""
    page = client.get("/import").text
    for suffix in (".csv", ".xlsx", ".docx", ".pdf"):
        assert suffix in page


def test_an_unsupported_file_still_gets_a_clear_refusal(client):
    """前端不拦了，后端必须拦得住、且说得清。"""
    from io import BytesIO

    result = client.post(
        "/import",
        data={"framework_id": "X-1", "name": "x"},
        files={"file": ("photo.jpg", BytesIO(b"\xff\xd8\xff"), "image/jpeg")},
    )
    assert "Only" in result.text or "not accepted" in result.text


def test_no_page_shows_raw_markdown(client):
    """`**粗体**` 写在 Python 字符串里，渲到页面上就是四个星号。

    我在这个仓库里犯过三次：CSS 注释里、导入的报错文案里、对话框的提示语里。
    模型页那条测试只盯它自己那一页，这条盯全部。

    注意这条只查**我们写的模板**，用户上传的正文里有星号是他的自由。
    """
    import re

    # 自己导入的条款页要单独走一遍——对话框只在那种页面上渲，
    # 而我最近一次犯就是犯在它的提示语里。
    _import_with_body(client)
    for path in ("/frameworks", "/import", "/f/NIST-CSF-2.0", f"/c/{CID}",
                 "/f/ACME-1", "/c/ACME-1:4.1",
                 "/f/NIST-CSF-2.0/assess", "/f/NIST-CSF-2.0/gap",
                 "/f/NIST-CSF-2.0/soa", "/documents", "/settings"):
        html = client.get(path).text
        assert not re.search(r"\*\*[^*\n]{1,40}\*\*", html), f"{path} 上有裸 markdown"
