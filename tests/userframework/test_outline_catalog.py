"""条款目录（catalog）与章节号是两套编号。见 2026-08-29 NIST.AI.100-1 导入

模型按「5.1 Govern」这种章节号切，会把 Table 1 整张吞进一条。
代码必须自己认出 GOVERN 1.1 / PR.AA-01 / AC-2 / A.5.1 / Article 9
这种带字母前缀的条款号，按行拆开——不靠模型下次碰巧认对，
也不为某一种框架写特判。

公司制度那种纯数字 5.1 / 3.2 不在此列，原样留给模型。
"""
from framework_reader.userframework.catalog import (
    apply_catalog, find_catalog_entries, parse_catalog_line,
)
from framework_reader.userframework.outline import (
    Span, outline_document, slice_lines,
)


def _span(ref, start, end, label="", parent=None):
    return Span(ref=ref, label=label, parent=parent, start=start, end=end)


# ---------- 认得出 / 认不出 ----------

def test_function_dot_number_is_a_catalog_id():
    assert parse_catalog_line("GOVERN 1.1: Legal and regulatory requirements")[0] == "GOVERN 1.1"


def test_a_category_without_rest_on_the_same_line_still_counts():
    assert parse_catalog_line("GOVERN 1:")[0] == "GOVERN 1"


def test_narrative_mention_is_not_a_catalog_id():
    """「GOVERN is a cross-cutting function」不是条款。"""
    assert parse_catalog_line("GOVERN is a cross-cutting function that is infused") is None
    assert parse_catalog_line("GOVERN , most users of the AI RMF would start") is None


def test_table_and_figure_captions_are_not_catalog_ids():
    assert parse_catalog_line("Table 1: Categories and subcategories") is None
    assert parse_catalog_line("TABLE 1 Categories and subcategories") is None
    assert parse_catalog_line("Figure 5 Functions organize AI risk") is None
    assert parse_catalog_line("Page 22") is None


def test_csf_80053_iso_and_article_shapes_are_recognized():
    assert parse_catalog_line("PR.AA-01: Identities and credentials")[0] == "PR.AA-01"
    assert parse_catalog_line("AC-2 Account Management")[0] == "AC-2"
    assert parse_catalog_line("AC-2(1) Automated System Account Management")[0] == "AC-2(1)"
    assert parse_catalog_line("A.5.1 Policies for information security")[0] == "A.5.1"
    assert parse_catalog_line("Article 9 Risk management system")[0] == "Article 9"
    assert parse_catalog_line("CC6.1 Logical and physical access")[0] == "CC6.1"


def test_a_bare_chapter_number_is_not_a_catalog_id():
    """公司制度的 5.1、3.2 继续走模型切分，这里不许抢。"""
    assert parse_catalog_line("5.1 Govern") is None
    assert parse_catalog_line("3.2 监控与告警") is None
    assert parse_catalog_line("五、账号管理") is None


# ---------- 从表里收出来 ----------

RMF = """\
RMF 1.0 as well as AI research and development conducted by NIST.
5.1 Govern
The GOVERN function cultivates a culture of risk management.
Table 1: Categories and subcategories for the GOVERN function.
GOVERN 1:
Policies, processes, and practices are in place.
GOVERN 1.1: Legal and regulatory requirements involving AI
are understood, managed, and documented.
GOVERN 1.2: The characteristics of trustworthy AI are integrated.
Categories Subcategories
Continued on next page
Page 22
Table 1: Categories and subcategories for the GOVERN function. (Continued)
GOVERN 1.3: Processes to determine the needed level of risk
management activities are in place.
5.2 Map
The MAP function establishes context.
Table 2: Categories and subcategories for the MAP function.
MAP 1: Context is established and understood.
MAP 1.1: Intended purposes are understood and documented.
"""


def test_harvest_finds_each_prefixed_row():
    refs = [e.ref for e in find_catalog_entries(RMF.splitlines())]
    assert refs == [
        "GOVERN 1", "GOVERN 1.1", "GOVERN 1.2", "GOVERN 1.3",
        "MAP 1", "MAP 1.1",
    ]


def test_a_lone_version_string_is_not_a_catalog_family():
    """PDF 换行后「AI RMF 1.0」变成行首「RMF 1.0」。只有一条，不是一张表。"""
    refs = [e.ref for e in find_catalog_entries(RMF.splitlines())]
    assert "RMF 1.0" not in refs


def test_a_wrapped_row_does_not_swallow_the_next_id():
    entries = {e.ref: e for e in find_catalog_entries(RMF.splitlines())}
    body = slice_lines(RMF, entries["GOVERN 1.1"].start, entries["GOVERN 1.1"].end)
    assert "Legal and regulatory" in body
    assert "GOVERN 1.2" not in body
    assert "trustworthy AI" not in body


def test_chrome_and_the_next_chapter_are_not_in_the_last_row_of_a_table():
    """GOVERN 1.3 后面是「5.2 Map」整章引言。吞进去就和模型犯同一个错。"""
    entries = {e.ref: e for e in find_catalog_entries(RMF.splitlines())}
    body = slice_lines(RMF, entries["GOVERN 1.3"].start, entries["GOVERN 1.3"].end)
    assert "needed level of risk" in body
    assert "5.2 Map" not in body
    assert "MAP function" not in body
    assert "Categories Subcategories" not in body


def test_a_child_points_at_its_catalog_parent():
    entries = {e.ref: e for e in find_catalog_entries(RMF.splitlines())}
    assert entries["GOVERN 1.1"].parent == "GOVERN 1"
    assert entries["GOVERN 1"].parent is None
    assert entries["MAP 1.1"].parent == "MAP 1"


# ---------- 模型把表吞进一章时，代码拆开 ----------

def test_a_multiword_chapter_heading_stops_the_last_row_of_the_table():
    """「6. AI RMF Profiles」这种多词标题也是新章，不能让最后一条 MANAGE
    把整章简介吞进去。"""
    doc = ("MANAGE 4.1: A go/no-go determination is made.\n"
           "MANAGE 4.2: Continual improvements are integrated.\n"
           "MANAGE 4.3: Incidents and errors are communicated.\n"
           "Categories Subcategories\n"
           "6. AI RMF Profiles\n"
           "Profiles illustrate how risk can be managed.")
    entries = find_catalog_entries(doc.splitlines())
    last = next(e for e in entries if e.ref == "MANAGE 4.3")
    body = slice_lines(doc, last.start, last.end)
    assert "Incidents and errors" in body
    assert "Profiles illustrate" not in body


def test_a_swallowed_table_is_split_out_of_the_chapter():
    """NIST.AI.100-1 实测：5.1 一条 7223 字，里面埋了全部 GOVERN 1.1–6.2。"""
    lines = RMF.splitlines()
    swallowed = [_span("5.1", 1, 16, label="Govern"),
                 _span("5.2", 17, 21, label="Map")]
    out, problems = apply_catalog(swallowed, lines)
    refs = [s.ref for s in out]
    assert "GOVERN 1.1" in refs
    assert "GOVERN 1.2" in refs
    assert "MAP 1.1" in refs
    assert "5.1" in refs and "5.2" in refs
    assert any(p.kind == "catalog" for p in problems)


def test_the_chapter_span_is_kept_as_a_parent_not_deleted():
    lines = RMF.splitlines()
    out, _ = apply_catalog([_span("5.1", 1, 16, label="Govern")], lines)
    chapter = next(s for s in out if s.ref == "5.1")
    child = next(s for s in out if s.ref == "GOVERN 1.1")
    assert chapter.start <= child.start and child.end <= 16


def test_already_split_rows_are_not_duplicated():
    lines = RMF.splitlines()
    entries = find_catalog_entries(lines)
    g11 = next(e for e in entries if e.ref == "GOVERN 1.1")
    existing = [_span("GOVERN 1.1", g11.start, g11.end, label="Legal")]
    out, _ = apply_catalog(existing, lines)
    assert [s.ref for s in out].count("GOVERN 1.1") == 1


# ---------- 别的框架同一条规则 ----------

def test_a_csf_style_list_is_split_the_same_way():
    doc = ("5 Protective Technology\n"
           "PR.AA-01: Identities and credentials are initiated.\n"
           "PR.AA-02: Identities are proofed and bound to credentials.\n"
           "PR.AA-03: Users are authenticated.")
    lines = doc.splitlines()
    out, _ = apply_catalog([_span("5", 1, 4, label="Protective Technology")], lines)
    assert {s.ref for s in out} >= {"PR.AA-01", "PR.AA-02", "PR.AA-03", "5"}


def test_iso_annex_and_80053_and_article_lists_split():
    doc = ("Annex A\n"
           "A.5.1 Policies for information security\n"
           "A.5.2 Information security roles\n"
           "AC-2 Account Management\n"
           "Article 9 Risk management system")
    refs = {e.ref for e in find_catalog_entries(doc.splitlines())}
    assert refs == {"A.5.1", "A.5.2", "AC-2", "Article 9"}


# ---------- 公司制度不动 ----------

POLICY = """五、账号管理
公司应当为每一名员工分配唯一账号，禁止共用。
离职当日停用。
六、口令策略
口令长度不少于 12 位。"""


def test_a_numeric_company_policy_is_left_alone():
    lines = POLICY.splitlines()
    original = [_span("5", 2, 3, label="账号管理"),
                _span("6", 5, 5, label="口令策略")]
    out, problems = apply_catalog(original, lines)
    assert out == original
    assert problems == []


def test_a_stray_prefixed_id_in_prose_does_not_trigger_a_harvest():
    """正文里偶尔冒出一句「见 Article 9」不够成一张条款表。"""
    doc = ("5.1 日志\n"
           "生产日志留存六个月。Article 9 of the Act 也提到了。\n"
           "5.2 告警")
    lines = doc.splitlines()
    original = [_span("5.1", 2, 2), _span("5.2", 3, 3)]
    out, problems = apply_catalog(original, lines)
    assert out == original
    assert problems == []


# ---------- 接进管线 ----------

class _Fake:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, messages, *, model, max_tokens=4096,
                 response_format=None):
        return self.reply


def test_outline_splits_a_swallowed_table_even_when_the_model_does_not():
    """模型只回了 5.1 / 5.2 两条。管线出来必须有 GOVERN 1.1。"""
    client = _Fake(
        '[{"ref":"5.1","label":"Govern","parent":null,"from":2,"to":16},'
        ' {"ref":"5.2","label":"Map","parent":null,"from":18,"to":21}]')
    result = outline_document(RMF, client=client, model="m")
    refs = [s.ref for s in result.spans]
    assert "GOVERN 1.1" in refs
    assert "MAP 1.1" in refs
    body = slice_lines(RMF, next(s.start for s in result.spans if s.ref == "GOVERN 1.1"),
                       next(s.end for s in result.spans if s.ref == "GOVERN 1.1"))
    assert "Legal and regulatory" in body
    assert "GOVERN 1.2" not in body
    assert any(p.kind == "catalog" for p in result.problems)


def test_outline_of_a_company_policy_does_not_grow_catalog_spans():
    client = _Fake(
        '[{"ref":"5","label":"账号管理","parent":null,"from":2,"to":3},'
        ' {"ref":"6","label":"口令策略","parent":null,"from":5,"to":5}]')
    result = outline_document(POLICY, client=client, model="m")
    assert [s.ref for s in result.spans] == ["5", "6"]
    assert all(p.kind != "catalog" for p in result.problems)
