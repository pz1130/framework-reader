"""文档导入。见 2026-08-25 AI 导入设计 §4、§5

模型注入假的。**预检不过时一个请求都不发**——跑到一半没钱了，那半份预览
是垃圾，钱也白花。
"""
import re
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader import crypto
from framework_reader.identity.store import IdentityStore
from framework_reader.llm.config import ModelConfig
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier
from framework_reader.userframework.outline import Outline, Problem, Span

DOC = ("五、账号管理\n公司应当为每一名员工分配唯一账号，禁止共用。\n"
       "离职当日停用。\n六、口令策略\n口令长度不少于 12 位。")


def _docx(text: str) -> bytes:
    """最小 .docx：一个 zip，里面一份 word/document.xml。"""
    import io
    import zipfile

    paragraphs = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            "word/document.xml",
            f"<w:document><w:body>{paragraphs}</w:body></w:document>")
    return buffer.getvalue()


def _one_span(text, *, client, model, on_chunk=None):
    """默认的假切分器：切出一条，外加一条未覆盖的告知。"""
    return Outline(
        spans=[Span(ref="5.1", label="账号管理", parent=None, start=2, end=3)],
        problems=[Problem("uncovered", "原文第 1–1 行没能切出条款。")],
        calls=1)


KEY = "sk-live-0123456789abcdef"


def _make(tmp_path, monkeypatch, outline_runner=_one_span, roles=("author",),
          with_key=True, shape_reply=None):
    """工厂。默认切出一条；要别的形状就传一个 runner 进来。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    calls = []
    shape_calls = []

    def counting(text, *, client, model, on_chunk=None):
        calls.append((text, model))
        return outline_runner(text, client=client, model=model,
                              on_chunk=on_chunk)

    def shape(sample, *, client, model):
        shape_calls.append(sample)
        return shape_reply if shape_reply is not None else "我看不懂这张表"

    identity = IdentityStore()
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                            roles=roles)
    if with_key:
        # 切分器是注入的假货，但 client 照样要组装得起来——
        # 没有 key 那条路是另一个用例（见下）。
        ModelConfig().set_key("deepseek", KEY, by="ann@acme.cn")
    app = create_app(db, outline_runner=counting, shape_runner=shape)
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"email": "ann@acme.cn",
                                "password": "pw-ann-ann-ann"})
    env = type("Env", (), {})()
    env.client, env.calls, env.config = client, calls, ModelConfig()
    env.identity, env.db = identity, db
    env.shape_calls = shape_calls
    return env


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _csrf(env) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', env.client.get("/frameworks").text)
    return found.group(1) if found else ""


@pytest.fixture(autouse=True)
def _clean_jobs():
    """切分任务是进程内全局的。上一个测试留下的会让下一个「已经在跑了」。"""
    from framework_reader.web import jobs

    jobs.reset()
    yield
    jobs.reset()


def _settle(env, result):
    """切分在后台跑。等它跑完，返回**预览页的地址**。

    没走后台那条（表格直接落库、报错页）就原样返回。
    """
    from framework_reader.web import jobs

    location = result.headers.get("location", "")
    if "/import/job/" not in location:
        return result
    job = jobs.get_outline(location.rsplit("/", 1)[1])
    assert job is not None
    job.wait(timeout=10)
    return env.client.get(location)


def _upload(env, data: bytes, name="f.docx"):
    return env.client.post(
        "/import",
        data={"framework_id": "ACME-1", "name": "ACME 制度", "csrf": _csrf(env)},
        files={"file": (name, BytesIO(data), "application/octet-stream")})


# ---------- 分流 ----------

def test_a_docx_lands_on_a_preview_not_in_the_library(env):
    """确认前不写库。这是整个预览环节存在的理由。"""
    from framework_reader.userframework.store import UserFrameworkStore

    result = _settle(env, _upload(env, _docx(DOC)))
    assert result.status_code == 303
    assert "/import/" in result.headers["location"]
    assert UserFrameworkStore().list_frameworks() == []


def test_a_document_upload_goes_through_a_progress_page(env):
    """一份 200 页的制度分几块跑几分钟。同步跑完再返回，
    浏览器那边就是一个转圈的白页。"""
    result = _upload(env, _docx(DOC))
    assert result.status_code == 303
    assert "/import/job/" in result.headers["location"]


def test_the_progress_page_refreshes_itself(env):
    from framework_reader.web import jobs

    location = _upload(env, _docx(DOC)).headers["location"]
    job = jobs.get_outline(location.rsplit("/", 1)[1])
    page = env.client.get(location).text
    if job.running:
        assert 'http-equiv="refresh"' in page


def test_the_progress_page_hands_over_when_it_is_done(env):
    result = _settle(env, _upload(env, _docx(DOC)))
    assert result.status_code == 303
    assert "/import/job/" not in result.headers["location"]


def test_a_failing_split_says_so_instead_of_spinning_forever(tmp_path,
                                                             monkeypatch):
    def boom(text, *, client, model, on_chunk=None):
        raise RuntimeError("模型端点变了")

    env = _make(tmp_path, monkeypatch, boom)
    location = _upload(env, _docx(DOC)).headers["location"]
    from framework_reader.web import jobs

    jobs.get_outline(location.rsplit("/", 1)[1]).wait(timeout=10)
    page = env.client.get(location).text
    assert "模型端点变了" in page
    assert 'http-equiv="refresh"' not in page


def test_an_unknown_job_is_404(env):
    assert env.client.get("/import/job/no-such-job").status_code == 404


def test_a_csv_still_goes_straight_into_the_library(env):
    """表格那条路一个字都不该变。"""
    from framework_reader.userframework.store import UserFrameworkStore

    result = _upload(env, "编号,标题\n1.1,账号管理\n".encode(), name="f.csv")
    assert result.status_code == 303
    assert result.headers["location"].endswith("/f/ACME-1")
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]
    assert env.calls == []          # 表格不调模型


def test_an_xlsx_is_still_a_table_not_a_document(env):
    """.xlsx 是表格，别因为它不是 .csv 就当文档喂给模型。"""
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["编号", "标题"])
    book.active.append(["1.1", "账号管理"])
    buffer = BytesIO()
    book.save(buffer)
    _upload(env, buffer.getvalue(), name="f.xlsx")
    assert env.calls == []


def test_the_document_text_is_what_reaches_the_splitter(env):
    """喂给模型的必须是抽出来的正文，不是原始字节。"""
    _upload(env, _docx(DOC))
    text, _ = env.calls[0]
    assert "公司应当为每一名员工分配唯一账号，禁止共用。" in text


# ---------- 抽不出来的文件 ----------

def test_a_pdf_that_is_not_a_pdf_says_so(env):
    result = _upload(env, b"not a pdf", name="f.pdf")
    assert "may not be a PDF" in result.text
    assert env.calls == []


def test_a_docx_with_no_words_is_refused(env):
    result = _upload(env, _docx(""), name="empty.docx")
    assert "no text" in result.text
    assert env.calls == []


# ---------- 花钱的闸：预检 ----------

def test_the_month_cap_is_checked_before_any_request_goes_out(env):
    """闸在前面。跑到一半没钱了，那半份预览是垃圾，钱也白花。

    上限最低是 1（`set_limits` 不收 0），所以先用掉那一格再试第二次。
    """
    env.config.set_limits(draft_cap_month=1, by="ann@acme.cn")
    _upload(env, _docx(DOC))                       # 用掉唯一那一格
    before = len(env.calls)
    result = _upload(env, _docx(DOC))
    assert len(env.calls) == before                # 第二次一个请求都没发
    assert "budget" in result.text or "limit" in result.text


def test_the_hourly_cap_is_checked_too(env):
    env.config.set_limits(draft_cap_hour=1, by="ann@acme.cn")
    _upload(env, _docx(DOC))
    before = len(env.calls)
    _upload(env, _docx(DOC))
    assert len(env.calls) == before


def test_a_refused_import_is_not_charged(env):
    """`charge_draft` 的规矩：拒了不记——拒了还扣，等于第二次更容易被拒。"""
    env.config.set_limits(draft_cap_month=1, by="ann@acme.cn")
    _upload(env, _docx(DOC))
    spent = env.config.spent_this_month()
    _upload(env, _docx(DOC))                       # 被拒
    assert env.config.spent_this_month() == spent


def test_a_successful_import_is_charged(env):
    _upload(env, _docx(DOC))
    assert env.config.spent_this_month() >= 1


# ---------- 权限 ----------

def test_an_admin_cannot_spend_the_money_by_uploading_a_word_file(tmp_path,
                                                                  monkeypatch):
    """permissions.py：admin 管系统，**不含起草与确认**——起草花的是组织的钱。

    表格导入不花钱，admin 有 framework:import 就够；文档导入调模型，
    门槛要跟起草一样。admin 要用就先给自己加 author，那一步会进审计日志。
    """
    env = _make(tmp_path, monkeypatch, roles=("admin",))
    result = _upload(env, _docx(DOC))
    assert env.calls == []
    assert "author" in result.text


def test_an_admin_can_still_import_a_table(tmp_path, monkeypatch):
    from framework_reader.userframework.store import UserFrameworkStore

    env = _make(tmp_path, monkeypatch, roles=("admin",))
    _upload(env, "编号,标题\n1.1,账号管理\n".encode(), name="f.csv")
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]


def test_no_key_configured_says_so_instead_of_crashing(tmp_path, monkeypatch):
    """管理员还没在模型页填 key。这该是一句人话，不是一个 500。"""
    env = _make(tmp_path, monkeypatch, with_key=False)
    result = _upload(env, _docx(DOC))
    assert "key" in result.text
    assert env.calls == []


def test_a_failed_import_does_not_keep_the_money(tmp_path, monkeypatch):
    """闸过了、钱记了、然后在更后面一步失败——那笔账留着就是白扣。"""
    env = _make(tmp_path, monkeypatch, with_key=False)
    _upload(env, _docx(DOC))
    assert env.config.spent_this_month() == 0


# ---------- 留痕 ----------

def test_the_split_is_written_to_the_audit_log(env):
    """一次真实出网、花组织的钱。设计 §4.1"""
    _settle(env, _upload(env, _docx(DOC)))
    events = [e["event"] for e in env.identity.audit(20)]
    assert "framework.outline" in events


def test_the_audit_line_says_which_file_and_how_many(env):
    _settle(env, _upload(env, _docx(DOC)))
    entry = next(e for e in env.identity.audit(20)
                 if e["event"] == "framework.outline")
    assert "ACME-1" in entry["detail"]
    assert "f.docx" in entry["detail"]


def test_the_document_text_never_enters_the_audit_log(env):
    """审计只记发生了这件事。制度正文不该沉淀进只追加的日志里。"""
    _upload(env, _docx(DOC))
    assert all("禁止共用" not in e["detail"] for e in env.identity.audit(20))


# ---------- 预览页 ----------

def _preview(env, data=None):
    return env.client.get(_draft_url(env, data)).text


def _draft_url(env, data=None) -> str:
    result = _settle(env, _upload(env, data or _docx(DOC)))
    return result.headers["location"]


def _draft_id(env, data=None) -> str:
    return _draft_url(env, data).rsplit("/", 1)[1]


def test_the_preview_shows_the_body_cut_from_the_document(env):
    """**地基在网页上的样子**：显示的正文就是原文第 2–3 行。"""
    assert "公司应当为每一名员工分配唯一账号，禁止共用。" in _preview(env)


def test_the_preview_says_how_many_it_cut(env):
    assert "Cut into 1 controls" in _preview(env)


def test_the_preview_says_nothing_is_written_yet(env):
    assert "Nothing is written until you confirm" in _preview(env)


def test_confirm_and_discard_are_also_at_the_top_of_the_preview(env):
    """条款一多就要拉到底才能点确认。顶上也放同一排。"""
    page = _preview(env)
    assert page.count("Import checked controls") == 2
    assert page.count("Discard this import") == 2
    assert page.find("Import checked controls") < page.find('class="prow"')


def test_the_preview_reports_the_lines_nobody_claimed(env):
    assert "原文第 1–1 行没能切出条款" in _preview(env)


def test_a_catalog_split_is_not_drawn_as_a_failure(tmp_path, monkeypatch):
    """「已按编号拆开」是处理结果。跟 not_json 挤在一串 ⚠ 里，
    人会以为 91 条拆开也是出错。"""
    def runner(text, *, client, model, on_chunk=None):
        return Outline(
            spans=[Span(ref="GOVERN 1.1", label="Legal", parent=None,
                        start=2, end=3)],
            problems=[Problem(
                "catalog",
                "原文里有 91 条带前缀的条款编号，已按编号拆开。")],
            calls=1)
    page = _preview(_make(tmp_path, monkeypatch, runner))
    assert "已按编号拆开" in page
    assert "⚠ 原文里有 91" not in page


def test_the_preview_shows_which_lines_each_clause_came_from(env):
    """对不上原文就没法核对。行号是核对的唯一抓手。"""
    assert "Source lines 2-3" in _preview(env)


def test_the_body_is_not_editable(env):
    """正文只读。能在这儿改就等于把「模型不许改写正文」从前门放进来。设计 §5.2"""
    page = _preview(env)
    assert 'name="body"' not in page
    assert "<textarea" not in page


def test_an_entry_with_a_label_starts_checked(env):
    assert 'value="0" checked' in _preview(env)


def test_an_entry_with_no_label_starts_unchecked(tmp_path, monkeypatch):
    """空标题多半是切歪了，但也可能是真条款——不替人决定，只是不默认勾上。"""
    def runner(text, *, client, model, on_chunk=None):
        return Outline(
            spans=[Span(ref="5.1", label="", parent=None, start=2, end=3)],
            problems=[], calls=1)

    env = _make(tmp_path, monkeypatch, runner)
    page = _preview(env)
    assert 'value="0" checked' not in page
    assert 'name="keep" value="0"' in page      # 框还在，只是没勾上


def test_the_first_entry_has_no_merge_button(tmp_path, monkeypatch):
    """第一条没有「上一条」可以合并。"""
    page = _preview(_make(tmp_path, monkeypatch))
    assert 'name="merge" value="0"' not in page


def test_an_unknown_draft_is_404(env):
    assert env.client.get("/import/no-such-draft").status_code == 404


# ---------- 确认落库 ----------

def _confirm(env, draft_id, **data):
    return env.client.post(f"/import/{draft_id}/confirm",
                           data={"csrf": _csrf(env), **data})


def test_the_body_in_the_library_is_the_body_from_the_document(env):
    """**地基，端到端。** 这条红了整份设计就白做了。"""
    from framework_reader.query.api import QueryAPI

    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep="0",
             **{"ref-0": "5.1", "label-0": "账号管理"})
    reader = QueryAPI(env.db)
    assert reader.control_body("ACME-1:5.1") == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_confirming_writes_the_checked_ones(env):
    from framework_reader.userframework.store import UserFrameworkStore

    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep="0",
             **{"ref-0": "5.1", "label-0": "账号管理"})
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]


def test_an_unchecked_entry_is_not_written(env):
    from framework_reader.userframework.store import UserFrameworkStore

    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1",
             **{"ref-0": "5.1", "label-0": "账号管理"})     # 没有 keep
    assert UserFrameworkStore().control_ids("ACME-1") == set()


def test_an_edited_ref_and_label_are_what_land(env):
    from framework_reader.userframework.store import UserFrameworkStore

    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep="0",
             **{"ref-0": "9.9", "label-0": "我改过的标题"})
    assert UserFrameworkStore().control_ids("ACME-1") == {"ACME-1:9.9"}


def test_the_draft_is_gone_after_confirming(env):
    from framework_reader.userframework.import_draft import ImportDraftStore

    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep="0",
             **{"ref-0": "5.1", "label-0": "账号管理"})
    assert ImportDraftStore().load(draft_id) is None


def test_confirming_with_nothing_checked_is_refused(env):
    """一条都不勾就落库，会种下一个空框架。"""
    draft_id = _draft_id(env)
    page = _confirm(env, draft_id, confirm="1",
                    **{"ref-0": "", "label-0": ""}).text
    assert "Tick at least one control" in page


def test_confirming_an_unknown_draft_is_404(env):
    assert _confirm(env, "no-such-draft", confirm="1").status_code == 404


def test_discarding_removes_the_draft(env):
    from framework_reader.userframework.import_draft import ImportDraftStore

    draft_id = _draft_id(env)
    env.client.get(f"/import/{draft_id}/discard")
    assert ImportDraftStore().load(draft_id) is None


def test_discarding_writes_nothing(env):
    from framework_reader.userframework.store import UserFrameworkStore

    draft_id = _draft_id(env)
    env.client.get(f"/import/{draft_id}/discard")
    assert UserFrameworkStore().list_frameworks() == []


# ---------- 合并 ----------

def _two_spans(text, *, client, model, on_chunk=None):
    return Outline(spans=[
        Span(ref="5.1", label="账号管理", parent=None, start=2, end=2),
        Span(ref="5.2", label="被多切的一刀", parent=None, start=3, end=3),
    ], problems=[], calls=1)


def test_merging_joins_the_two_line_ranges(tmp_path, monkeypatch):
    """多切了一刀是切歪的主要形式。合并把两段行号接起来，正文自动重算。"""
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _two_spans)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="1",
             **{"ref-0": "5.1", "label-0": "账号管理",
                "ref-1": "5.2", "label-1": "被多切的一刀"})
    draft = ImportDraftStore().load(draft_id)
    assert len(draft.spans) == 1
    assert (draft.spans[0].start, draft.spans[0].end) == (2, 3)


def test_merging_keeps_the_upper_ref_and_label(tmp_path, monkeypatch):
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _two_spans)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="1",
             **{"ref-0": "5.1", "label-0": "账号管理",
                "ref-1": "5.2", "label-1": "被多切的一刀"})
    span = ImportDraftStore().load(draft_id).spans[0]
    assert span.ref == "5.1"
    assert span.label == "账号管理"


def test_merging_swallows_the_gap_between_them(tmp_path, monkeypatch):
    """两段之间没被覆盖的行，合并时一并并进来——那正是想要的。"""
    from framework_reader.userframework.import_draft import ImportDraftStore

    def runner(text, *, client, model, on_chunk=None):
        return Outline(spans=[
            Span(ref="5.1", label="a", parent=None, start=1, end=1),
            Span(ref="5.2", label="b", parent=None, start=4, end=5),
        ], problems=[], calls=1)

    env = _make(tmp_path, monkeypatch, runner)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="1",
             **{"ref-0": "5.1", "label-0": "a", "ref-1": "5.2", "label-1": "b"})
    span = ImportDraftStore().load(draft_id).spans[0]
    assert (span.start, span.end) == (1, 5)


def test_the_merged_body_is_still_verbatim(tmp_path, monkeypatch):
    """合并改的是边界，不是正文。合完那几行还得逐字对得上。"""
    from framework_reader.query.api import QueryAPI

    env = _make(tmp_path, monkeypatch, _two_spans)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="1",
             **{"ref-0": "5.1", "label-0": "账号管理",
                "ref-1": "5.2", "label-1": "被多切的一刀"})
    _confirm(env, draft_id, confirm="1", keep="0",
             **{"ref-0": "5.1", "label-0": "账号管理"})
    assert QueryAPI(env.db).control_body("ACME-1:5.1") == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_edits_made_before_merging_are_not_lost(tmp_path, monkeypatch):
    """改完标题再点合并，改动不能被丢——那会让人以为白改了。"""
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _two_spans)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="1",
             **{"ref-0": "5.1", "label-0": "我改过的",
                "ref-1": "5.2", "label-1": "b"})
    assert ImportDraftStore().load(draft_id).spans[0].label == "我改过的"


# ---------- 只当分组标题的父条款 ----------

def _parent_and_child(text, *, client, model, on_chunk=None):
    """父条款只是个分组标题，自己没有正文。

    这里给的是 `validate()` **截过之后**的形状（父条款 end < start），
    因为注入 runner 会把整个 outline_document 替掉，校验那一段不会跑。
    假货要长得像真货的产物，否则测的是假货自己。
    """
    return Outline(spans=[
        Span(ref="2", label="账号与权限", parent=None, start=1, end=0),
        Span(ref="2.1", label="账号管理", parent="2", start=2, end=3),
    ], problems=[], calls=1)


def test_a_grouping_clause_says_so_instead_of_showing_a_blank(tmp_path,
                                                              monkeypatch):
    """父条款截到第一个子条款之前，截完是空的。显示一片空白，
    人会以为是 bug；说清楚它只是个分组标题，人就知道该怎么办。"""
    env = _make(tmp_path, monkeypatch, _parent_and_child)
    page = _preview(env)
    assert "has no body text of its own" in page


def test_a_grouping_clause_does_not_print_a_backwards_line_range(tmp_path,
                                                                 monkeypatch):
    """「原文 1–0 行」是胡说。"""
    import re

    env = _make(tmp_path, monkeypatch, _parent_and_child)
    page = _preview(env)
    for lo, hi in re.findall(r"Source lines (\d+)-(\d+)", page):
        assert int(lo) <= int(hi)


def test_the_child_still_shows_its_body(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, _parent_and_child)
    assert "公司应当为每一名员工分配唯一账号，禁止共用。" in _preview(env)


def test_a_grouping_clause_can_still_be_imported(tmp_path, monkeypatch):
    """它没有正文，但它是树上的一个节点——子条款要挂在它下面。"""
    from framework_reader.userframework.store import UserFrameworkStore

    env = _make(tmp_path, monkeypatch, _parent_and_child)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep=["0", "1"],
             **{"ref-0": "2", "label-0": "账号与权限",
                "ref-1": "2.1", "label-1": "账号管理"})
    assert UserFrameworkStore().control_ids("ACME-1") == {"ACME-1:2", "ACME-1:2.1"}


# ---------- 表头认不出来时，让模型看一眼 ----------
#
# 「表头里找不到「编号」这一列，把上面那几行删掉再传」——让用户去改自己的表，
# 就是把我们的问题推给他。而且他不会改。

def _xlsx(rows) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


MESSY = [
    ["ACME 信息安全控制清单", "", "", ""],
    ["版本 2.0　制表：安全部", "", "", ""],
    ["控制编号", "控制名称", "上级", "要求正文"],
    ["3.1", "账号管理", "", "公司应当为每一名员工分配唯一账号。"],
    ["3.1.1", "账号申请", "3.1", "由部门主管提交申请。"],
]

CLEAN = [["编号", "标题"], ["3.1", "账号管理"]]


def _make_table(tmp_path, monkeypatch, shape_reply, roles=("author",)):
    """`shape_runner` 换掉那次「看一眼这张表」的模型调用。"""
    env = _make(tmp_path, monkeypatch, roles=roles)
    env.shape_replies = [shape_reply]
    return env


def test_a_clean_table_never_calls_the_model(env):
    """表头在第一行那条路是免费的、瞬时的、不会错。不许被一次模型调用吃掉。"""
    from framework_reader.userframework.store import UserFrameworkStore

    _upload(env, _xlsx(CLEAN), name="clean.xlsx")
    assert env.shape_calls == []
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]


def test_a_messy_table_goes_to_the_model_and_lands_on_a_preview(tmp_path,
                                                                monkeypatch):
    env = _make(tmp_path, monkeypatch, shape_reply=(
        '{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
        '"parent_col":2,"body_col":3}'))
    result = _upload(env, _xlsx(MESSY), name="messy.xlsx")
    assert result.status_code == 303
    assert "/import/" in result.headers["location"]
    assert len(env.shape_calls) == 1


def test_the_preview_shows_the_cells_verbatim(tmp_path, monkeypatch):
    """**地基。** 模型只给了下标，字是代码从原表取的。"""
    env = _make(tmp_path, monkeypatch, shape_reply=(
        '{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
        '"parent_col":2,"body_col":3}'))
    location = _upload(env, _xlsx(MESSY), name="messy.xlsx").headers["location"]
    page = env.client.get(location).text
    assert "公司应当为每一名员工分配唯一账号。" in page
    assert 'value="3.1.1"' in page


def test_the_rows_above_the_header_do_not_become_clauses(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, shape_reply=(
        '{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
        '"parent_col":2,"body_col":3}'))
    location = _upload(env, _xlsx(MESSY), name="messy.xlsx").headers["location"]
    assert "制表：安全部" not in env.client.get(location).text


def test_a_shape_the_model_got_wrong_falls_back_to_the_plain_error(tmp_path,
                                                                   monkeypatch):
    """模型指到了第 99 行。退回今天那条人话报错，不是 500。"""
    env = _make(tmp_path, monkeypatch, shape_reply=(
        '{"kind":"table","header_row":99,"id_col":0,"label_col":1,'
        '"parent_col":null,"body_col":null}'))
    result = _upload(env, _xlsx(MESSY), name="messy.xlsx")
    assert "header" in result.text
    assert result.status_code == 200


def test_model_garbage_falls_back_to_the_plain_error(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, shape_reply="我看不懂这张表")
    result = _upload(env, _xlsx(MESSY), name="messy.xlsx")
    assert "header" in result.text


def test_a_document_verdict_runs_the_outline_pipeline(tmp_path, monkeypatch):
    """一份制度贴进了 Excel。硬凑列映射会生成一整张假清单。"""
    env = _make(tmp_path, monkeypatch,
                shape_reply='{"kind":"document","why":"一行一段正文"}')
    location = _upload(env, _xlsx(MESSY), name="messy.xlsx").headers["location"]
    assert location
    assert env.calls, "该走文档切分管线"


def test_the_table_shape_call_is_charged_and_audited(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, shape_reply=(
        '{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
        '"parent_col":2,"body_col":3}'))
    _upload(env, _xlsx(MESSY), name="messy.xlsx")
    assert env.config.spent_this_month() >= 1
    events = [e["event"] for e in env.identity.audit(20)]
    assert "framework.tableshape" in events


def test_an_admin_cannot_trigger_the_model_with_a_messy_table(tmp_path,
                                                              monkeypatch):
    """回退路径调模型 = 花组织的钱，门槛跟文档导入一样。"""
    env = _make(tmp_path, monkeypatch, roles=("admin",), shape_reply=(
        '{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
        '"parent_col":2,"body_col":3}'))
    result = _upload(env, _xlsx(MESSY), name="messy.xlsx")
    assert env.shape_calls == []
    assert "author" in result.text


# ---------- 重号：预览页就要说，别等确认时炸 ----------

def _dup_refs(text, *, client, model, on_chunk=None):
    return Outline(spans=[
        Span(ref="T1", label="Transparency", parent=None, start=1, end=1),
        Span(ref="T1", label="Transparency", parent=None, start=2, end=2),
    ], problems=[], calls=1)


def test_confirming_duplicate_refs_does_not_blow_up(tmp_path, monkeypatch):
    """`user_control.id` 是主键。重号在落库那一刻是 IntegrityError——
    预览页看着没问题，点确认才崩，而那时候人已经改了半天。"""
    env = _make(tmp_path, monkeypatch, _dup_refs)
    draft_id = _draft_id(env)
    result = _confirm(env, draft_id, confirm="1", keep=["0", "1"],
                      **{"ref-0": "T1", "label-0": "a",
                         "ref-1": "T1", "label-1": "b"})
    assert result.status_code in (200, 400)
    assert "T1" in result.text


def test_the_duplicate_is_named_so_you_know_which_to_change(tmp_path,
                                                            monkeypatch):
    env = _make(tmp_path, monkeypatch, _dup_refs)
    draft_id = _draft_id(env)
    page = _confirm(env, draft_id, confirm="1", keep=["0", "1"],
                    **{"ref-0": "T1", "label-0": "a",
                       "ref-1": "T1", "label-1": "b"}).text
    assert "Duplicate IDs" in page


def test_nothing_is_written_when_refs_collide(tmp_path, monkeypatch):
    """半张框架比没有框架糟。"""
    from framework_reader.userframework.store import UserFrameworkStore

    env = _make(tmp_path, monkeypatch, _dup_refs)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, confirm="1", keep=["0", "1"],
             **{"ref-0": "T1", "label-0": "a", "ref-1": "T1", "label-1": "b"})
    assert UserFrameworkStore().list_frameworks() == []


def test_an_empty_ref_is_refused_too(tmp_path, monkeypatch):
    """编号是控制 ID 的一半，空的落库会变成「ACME-1:」。"""
    env = _make(tmp_path, monkeypatch, _dup_refs)
    draft_id = _draft_id(env)
    page = _confirm(env, draft_id, confirm="1", keep=["0"],
                    **{"ref-0": "", "label-0": "a",
                       "ref-1": "T1", "label-1": "b"}).text
    assert "IDs cannot be empty" in page


# ---------- 补出来的编号与标题要标出来 ----------
#
# 「谁写的要能看出来」是这个产品的地基。条款页那套「AI 初稿」是同一条规矩：
# 编号和标题可以由 AI 起（否则条款存不进库），但人必须一眼看得出哪些是。

def _derived(text, *, client, model, on_chunk=None):
    return Outline(spans=[
        Span(ref="3.2", label="日志与监控", parent=None, start=1, end=1,
             ref_from="original", label_from="original"),
        Span(ref="3.2.1", label="日志留存期限", parent="3.2", start=2, end=3,
             ref_from="derived", label_from="derived"),
    ], problems=[], calls=1)


def test_a_derived_ref_is_marked_on_the_preview(tmp_path, monkeypatch):
    env = _make(tmp_path, monkeypatch, _derived)
    assert "Named by AI" in _preview(env)


def test_an_original_ref_carries_no_mark(tmp_path, monkeypatch):
    """抄来的不该被标成 AI 起的——那会让人怀疑自己原文里的编号。"""
    import re

    env = _make(tmp_path, monkeypatch, _derived)
    page = _preview(env)
    block = page[page.index('value="3.2"'):page.index('value="3.2.1"')]
    assert "Named by AI" not in block


def test_the_mark_survives_the_round_trip(tmp_path, monkeypatch):
    """草稿落盘再读回来，标记不能丢——丢了就成了「原文里就有」。"""
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _derived)
    draft_id = _draft_id(env)
    spans = ImportDraftStore().load(draft_id).spans
    assert spans[1].ref_from == "derived"
    assert spans[1].label_from == "derived"


def test_editing_a_derived_ref_makes_it_yours(tmp_path, monkeypatch):
    """人改过之后就不再是 AI 起的了。"""
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _derived)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="",
             **{"ref-0": "3.2", "label-0": "日志与监控",
                "ref-1": "9.9", "label-1": "我改的标题"})
    spans = ImportDraftStore().load(draft_id).spans
    assert spans[1].ref_from == "practitioner"
    assert spans[1].label_from == "practitioner"


def test_an_untouched_derived_ref_stays_derived(tmp_path, monkeypatch):
    from framework_reader.userframework.import_draft import ImportDraftStore

    env = _make(tmp_path, monkeypatch, _derived)
    draft_id = _draft_id(env)
    _confirm(env, draft_id, merge="",
             **{"ref-0": "3.2", "label-0": "日志与监控",
                "ref-1": "3.2.1", "label-1": "日志留存期限"})
    assert ImportDraftStore().load(draft_id).spans[1].ref_from == "derived"
