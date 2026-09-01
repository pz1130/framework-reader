"""模型漏掉的大段（附录、摘要）和条款被截短的续行。

NIST.AI.100-1 第二次切出 113 条之后，1302–1620 行整段附录是空的，
3.3 在 553 行截住、把 554–559 的续行报成漏切。条款表已经按编号拆了，
这两件事代码能补，不必再花一次模型。
"""
from framework_reader.userframework.outline import (
    Span, close_small_gaps, fill_heading_holes, outline_document,
    slice_lines, uncovered,
)


def _span(ref, start, end, label=""):
    return Span(ref=ref, label=label, parent=None, start=start, end=end)


# ---------- 续行并回上一条 ----------

def test_a_few_prose_lines_between_clauses_join_the_previous():
    """3.3 写到一半，3.4 标题出现；中间 6 行是 3.3 的续行，不是新条款。"""
    lines = (
        ["3.3 Secure and Resilient"]
        + ["resilience sentence."] * 2
        + ["Security and resilience are related but distinct."] * 4
        + ["3.4 Accountable and Transparent"]
        + ["Accountability presupposes transparency."]
    )
    spans = [_span("3.3", 2, 3), _span("3.4", 9, 9)]
    out = close_small_gaps(spans, lines)
    three = next(s for s in out if s.ref == "3.3")
    assert three.end == 7
    body = slice_lines("\n".join(lines), three.start, three.end)
    assert "distinct" in body
    assert "3.4 Accountable" not in body


def test_a_chunk_boundary_continuation_joins_the_previous():
    """分块切在 3.3 句子中间：第一块停在 547，续行有 13 行才到 3.4。
    超过旧的 8 行上限就会留下「548–560 没切出条款」。"""
    lines = (
        ["3.3 Secure and Resilient"]
        + ["resilience sentence that was in the first chunk."]
        + [f"continuation line {i} of the same clause." for i in range(12)]
        + ["3.4 Accountable and Transparent"]
        + ["Accountability presupposes transparency."]
    )
    # 第一块只看到标题+一句；3.4 从标题的下一行起（模型跳过标题行）
    heading_3_4 = 3 + 12  # line index of 3.4 title
    spans = [_span("3.3", 2, 2),
             _span("3.4", heading_3_4 + 1, heading_3_4 + 1,
                   label="Accountable and Transparent")]
    out = close_small_gaps(spans, lines)
    three = next(s for s in out if s.ref == "3.3")
    assert three.end == heading_3_4 - 1
    body = slice_lines("\n".join(lines), three.start, three.end)
    assert "continuation line 0" in body
    assert "3.4 Accountable" not in body


def test_a_one_line_title_hole_is_not_swallowed():
    """公司制度里上一条和下一条之间那行是下一条的标题。并回去就脏了。"""
    lines = ["五、账号管理", "一人一号。", "六、口令策略", "十二位。"]
    original = [_span("5", 2, 2), _span("6", 4, 4)]
    assert close_small_gaps(original, lines) == original


# ---------- 大洞里按标题补切 ----------

HOLE = """\
Categories Subcategories
6. AI RMF Profiles
Profiles are implementations of the functions for a specific setting.
Page 33
Appendix A:
Descriptions of AI Actor Tasks
Design actors create the concept and objectives.
Appendix B:
How AI Risks Differ from Traditional Software Risks
AI risks can change over time.
"""


def test_appendices_in_a_leftover_hole_become_clauses():
    """模型把附录当「附件清单」跳过。大洞里看到 Appendix A 就要切。"""
    lines = HOLE.splitlines()
    # 整段都没人认领
    out = fill_heading_holes([], lines)
    refs = [s.ref for s in out]
    assert "6" in refs
    assert "A" in refs
    assert "B" in refs
    body_a = slice_lines(HOLE, next(s.start for s in out if s.ref == "A"),
                         next(s.end for s in out if s.ref == "A"))
    assert "Design actors" in body_a
    assert "Appendix B" not in body_a


def test_toc_lines_are_not_harvested_as_appendices():
    """目录里的「Appendix A: … 35」带页码，不是正文标题。"""
    lines = [
        "Table of Contents",
        "Appendix A: Descriptions of AI Actor Tasks 35",
        "Appendix B: How AI Risks Differ 38",
        "Executive Summary",
        "AI technologies have significant potential to transform society.",
    ]
    out = fill_heading_holes([], lines)
    refs = [s.ref for s in out]
    assert "A" not in refs
    assert "B" not in refs
    assert any(s.label == "Executive Summary" or s.ref == "Summary" for s in out)


def test_a_numbered_list_inside_an_appendix_is_not_a_new_section():
    """「6. Be useful to a wide range…」是附录里的条目，不是第 6 章。"""
    lines = [
        "Appendix D:",
        "Attributes of the AI RMF",
        "6. Be useful to a wide range of perspectives, sectors, and technology domains. The AI",
        "community contributed.",
    ]
    out = fill_heading_holes([], lines)
    assert [s.ref for s in out] == ["D"]
    body = slice_lines("\n".join(lines), out[0].start, out[0].end)
    assert "Be useful" in body


def test_existing_spans_are_not_duplicated_by_heading_fill():
    lines = ["5. AI RMF Core", "The Core provides outcomes.", "6. AI RMF Profiles", "Profiles help."]
    existing = [_span("5", 2, 2, label="AI RMF Core")]
    out = fill_heading_holes(existing, lines)
    assert [s.ref for s in out].count("5") == 1
    assert "6" in [s.ref for s in out]


# ---------- 封面洞的说法 ----------

def test_a_leading_cover_hole_is_called_front_matter_not_a_missed_clause():
    lines = (
        ["NIST AI 100-1", "Framework", "Table of Contents", "1 Framing Risk 4"]
        + ["toc"] * 5
        + ["1 Framing Risk", "AI risk management offers a path."]
    )
    spans = [_span("1", 11, 12)]
    problems = uncovered(spans, total_lines=len(lines), lines=lines)
    assert any(p.kind == "front_matter" or "封面" in p.detail for p in problems)
    assert not any("第 1–10 行没能切出条款" in p.detail for p in problems)


# ---------- 管线 ----------

class _Fake:
    def complete(self, system, messages, *, model, max_tokens=4096,
                 response_format=None):
        # 模型只切了正文中间，附录整段没碰——跟实测一样。
        return ('[{"ref":"5.1","label":"Govern","parent":null,"from":2,"to":2}]')


def test_outline_picks_up_appendices_the_model_skipped():
    doc = ("5.1 Govern\n"
           "The GOVERN function.\n"
           "6. AI RMF Profiles\n"
           "Profiles are implementations of the functions.\n"
           "Appendix A:\n"
           "Descriptions of AI Actor Tasks\n"
           "Design actors create the concept.")
    result = outline_document(doc, client=_Fake(), model="m")
    refs = [s.ref for s in result.spans]
    assert "6" in refs
    assert "A" in refs
