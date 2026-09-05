"""边界校验与未覆盖行。见 2026-08-25 AI 导入设计 §2.2

一条都不信模型。**丢和降是两种不同的处理，别混**：越界的条款没有可信的
正文，只能丢；上级指错的条款正文是好的，丢掉它等于把用户的一条制度弄丢了。
"""
from framework_reader.userframework.outline import Span, uncovered, validate


def _span(ref="5.1", label="账号管理", parent=None, start=1, end=2):
    return Span(ref=ref, label=label, parent=parent, start=start, end=end)


# ---------- 越界：丢 ----------

def test_a_clean_list_passes_untouched():
    spans = [_span(start=1, end=2), _span(ref="5.2", start=3, end=4)]
    kept, problems = validate(spans, total_lines=10)
    assert kept == spans
    assert problems == []


def test_an_end_past_the_last_line_is_dropped():
    kept, problems = validate([_span(start=1, end=99)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]
    assert "5.1" in problems[0].detail


def test_a_start_below_one_is_dropped():
    kept, problems = validate([_span(start=0, end=3)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]


def test_an_end_before_its_start_is_dropped():
    kept, problems = validate([_span(start=7, end=3)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]


def test_the_message_says_how_long_the_document_actually_is():
    """「行号 1–99 越界」不够——用户要知道越出了什么。"""
    _, problems = validate([_span(start=1, end=99)], total_lines=10)
    assert "10" in problems[0].detail


# ---------- 重叠：丢后面那条 ----------

def test_an_overlapping_span_is_dropped_and_the_earlier_one_kept():
    """留前面那条：它的边界已经被前一条确认过，后面那条才是可疑的。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=5), _span(ref="5.2", start=4, end=8)],
        total_lines=10)
    assert [s.ref for s in kept] == ["5.1"]
    assert [p.kind for p in problems] == ["overlap"]
    assert "5.2" in problems[0].detail


def test_touching_but_not_overlapping_is_fine():
    """1–5 和 6–10 是相邻不是重叠。差一错在这儿会吃掉一半条款。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=5), _span(ref="5.2", start=6, end=10)],
        total_lines=10)
    assert len(kept) == 2
    assert problems == []


def test_results_come_back_sorted_by_line_even_if_the_model_shuffled_them():
    kept, _ = validate(
        [_span(ref="5.2", start=5, end=6), _span(ref="5.1", start=1, end=2)],
        total_lines=10)
    assert [s.ref for s in kept] == ["5.1", "5.2"]


# ---------- 上级指错：降级，不丢 ----------

def test_a_parent_that_is_not_in_the_list_is_downgraded_not_dropped():
    """上级指错了，这条条款本身还是好的。降成顶层，并说一声。"""
    kept, problems = validate(
        [_span(ref="5.1.1", parent="4.9", start=1, end=2)], total_lines=10)
    assert len(kept) == 1
    assert kept[0].parent is None
    assert [p.kind for p in problems] == ["bad_parent"]


def test_a_parent_that_is_in_the_list_survives():
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=2),
         _span(ref="5.1.1", parent="5.1", start=3, end=4)],
        total_lines=10)
    assert kept[1].parent == "5.1"
    assert problems == []


def test_a_span_pointing_at_itself_as_parent_is_downgraded():
    kept, problems = validate(
        [_span(ref="5.1", parent="5.1", start=1, end=2)], total_lines=10)
    assert kept[0].parent is None
    assert [p.kind for p in problems] == ["bad_parent"]


def test_a_parent_that_was_itself_dropped_is_downgraded():
    """上级越界被丢了，子条款不能挂在一个不存在的编号上。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=99),
         _span(ref="5.1.1", parent="5.1", start=1, end=2)],
        total_lines=10)
    assert [s.ref for s in kept] == ["5.1.1"]
    assert kept[0].parent is None
    assert {p.kind for p in problems} == {"out_of_range", "bad_parent"}


# ---------- 重号：留下但报出来 ----------

def test_a_duplicate_ref_is_kept_but_flagged():
    """重号在真实制度里会出现（附录里又编了一遍 1.1）。
    落库时会撞，所以这里要先说出来，而不是替人删一条。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=2), _span(ref="5.1", start=3, end=4)],
        total_lines=10)
    assert len(kept) == 2
    assert {s.ref for s in kept} == {"5.1", "5.1-2"}
    assert any("5.1-2" in p.detail for p in problems)


def test_a_nested_duplicate_is_namespaced_under_its_parent():
    """NIST.AI.100-1：章节 1 Framing Risk 和附录 D 里「1. Be risk-based」撞号。
    嵌套那条改成上级.原号，人不用在预览里改六个框。"""
    kept, problems = validate(
        [_span(ref="1", start=1, end=4, label="Framing Risk"),
         _span(ref="D", start=10, end=20, label="Attributes"),
         _span(ref="1", start=12, end=13, parent="D", label="risk-based")],
        total_lines=20)
    refs = {s.ref: s for s in kept}
    assert "1" in refs and refs["1"].label == "Framing Risk"
    assert "D.1" in refs
    assert refs["D.1"].parent == "D"
    assert any("D.1" in p.detail for p in problems)


def test_two_empty_refs_are_not_duplicates():
    """空编号是「原文没编号」，不是同一个号。人会在预览页补。"""
    kept, problems = validate(
        [_span(ref="", start=1, end=2), _span(ref="", start=3, end=4)],
        total_lines=10)
    assert len(kept) == 2
    assert problems == []


# ---------- 未覆盖的行 ----------

def test_lines_nobody_claimed_are_reported_with_their_numbers():
    """`importer.py`：坏行一律报错并指出行号，绝不静默跳过——
    静默跳过的结果是用户以为全导进去了。同一条规矩。"""
    problems = uncovered([_span(start=1, end=5)], total_lines=12)
    assert [p.kind for p in problems] == ["uncovered"]
    assert "6" in problems[0].detail and "12" in problems[0].detail


def test_full_coverage_reports_nothing():
    problems = uncovered(
        [_span(ref="a", start=1, end=5), _span(ref="b", start=6, end=10)],
        total_lines=10)
    assert problems == []


def test_a_hole_in_the_middle_is_reported():
    problems = uncovered(
        [_span(ref="a", start=1, end=3), _span(ref="b", start=8, end=10)],
        total_lines=10)
    assert len(problems) == 1
    assert "4" in problems[0].detail and "7" in problems[0].detail


def test_each_big_hole_gets_its_own_line():
    problems = uncovered(
        [_span(ref="a", start=4, end=6), _span(ref="b", start=13, end=15)],
        total_lines=20)
    # 1–3、7–12、16–20 都是三行以上，各报各的
    assert len(problems) == 3


# ---------- 单行洞要折叠，否则真正的漏切会被淹掉 ----------
#
# 实测（Task 14）：一份 31 行的制度报出 7 条未覆盖，其中 5 条是**条款标题行**
# ——标题不进正文是设计要的。一份 600 行的真制度会报出几十条，
# 而「整章漏了」这种真问题就混在里面看不见了。

def test_a_one_line_hole_is_not_reported_on_its_own():
    """条款标题行每条都会留一个单行洞。逐个报出来等于什么都没报。"""
    problems = uncovered(
        [_span(ref="a", start=2, end=3), _span(ref="b", start=5, end=6)],
        total_lines=6)
    assert len(problems) == 1
    assert "more spot(s)" in problems[0].detail


def test_the_folded_line_says_how_many_there_were():
    """折叠不等于隐瞒。数目要说出来。"""
    problems = uncovered(
        [_span(ref="a", start=2, end=2), _span(ref="b", start=4, end=4),
         _span(ref="c", start=6, end=6)],
        total_lines=6)
    assert "3 more spot(s)" in problems[0].detail


def test_a_two_line_hole_folds_too():
    """章标题 + 条款标题连着，就是两行。也是噪声。"""
    problems = uncovered([_span(ref="a", start=3, end=9)], total_lines=9)
    assert len(problems) == 1
    assert "more spot(s)" in problems[0].detail


def test_a_three_line_hole_is_reported_in_full():
    """三行以上就可能是真漏了一条条款，要指名道姓。"""
    problems = uncovered([_span(ref="a", start=4, end=9)], total_lines=9)
    assert len(problems) == 1
    assert "Lines 1–3" in problems[0].detail


def test_a_chrome_only_hole_is_folded_even_when_three_or_four_lines():
    """PDF 表翻页留下「Categories / Continued / Page N / Table 1 (Continued)」。
    点名这四行会把预览页刷成一串 ⚠，而真漏切被淹掉。"""
    lines = [
        "GOVERN 1.4: outcomes are established.",
        "Categories Subcategories",
        "Continued on next page",
        "Page 22",
        "Table 1: Categories and subcategories. (Continued)",
        "GOVERN 1.5: Ongoing monitoring.",
    ]
    problems = uncovered(
        [_span(ref="GOVERN 1.4", start=1, end=1),
         _span(ref="GOVERN 1.5", start=6, end=6)],
        total_lines=6, lines=lines)
    assert not any("第 2–5 行" in p.detail for p in problems)
    assert any("more spot(s)" in p.detail for p in problems)


def test_big_holes_and_folded_ones_coexist():
    problems = uncovered(
        [_span(ref="a", start=5, end=6), _span(ref="b", start=8, end=9)],
        total_lines=9)
    details = " ".join(p.detail for p in problems)
    assert "Lines 1–4" in details          # 大洞点名
    assert "1 more spot(s)" in details          # 第 7 行折叠


def test_the_folded_line_explains_why_they_are_probably_fine():
    """不解释的话，一句「另有 12 处」只会让人不安。"""
    problems = uncovered([_span(ref="a", start=2, end=3)], total_lines=3)
    assert "clause titles" in problems[0].detail


def test_nothing_extracted_at_all_reports_the_whole_document():
    problems = uncovered([], total_lines=40)
    assert len(problems) == 1
    assert "1" in problems[0].detail and "40" in problems[0].detail


def test_an_empty_document_reports_nothing():
    assert uncovered([], total_lines=0) == []


# ---------- 条款是树，不是一串 ----------
#
# 实测（用户导入一份国标框架 PDF）：模型切对了——3.2.2（1044–1064）下面有
# 子项 a(1045–1050)、b(1052–1057)、c(1061–1064)。而 validate() 把这 184 条
# 子条款全当成「重叠」丢了，一份文档一半内容没进来。
#
# `Span.parent` 这个字段本来就是为层级存在的，`add_framework` 收的也是
# (编号, 标题, 父编号, 正文) 四元组——是中间这一层把树拍平了。

def test_a_span_fully_inside_another_is_a_child_not_an_overlap():
    kept, problems = validate(
        [_span(ref="3.2.2", start=10, end=30),
         _span(ref="a", start=11, end=16)],
        total_lines=40)
    assert [s.ref for s in kept] == ["3.2.2", "a"]
    assert problems == []


def test_the_child_gets_its_parent_filled_in():
    """模型常常不填 parent。包含关系本身就说明了谁是谁的上级。"""
    kept, _ = validate(
        [_span(ref="3.2.2", start=10, end=30),
         _span(ref="a", parent=None, start=11, end=16)],
        total_lines=40)
    assert kept[1].parent == "3.2.2"


def test_a_parent_the_model_did_give_is_not_overwritten():
    kept, _ = validate(
        [_span(ref="3.2", start=5, end=40),
         _span(ref="3.2.2", parent="3.2", start=10, end=30),
         _span(ref="a", parent="3.2.2", start=11, end=16)],
        total_lines=40)
    assert [s.parent for s in kept] == [None, "3.2", "3.2.2"]


def test_three_levels_deep_all_survive():
    kept, problems = validate(
        [_span(ref="3", start=1, end=40),
         _span(ref="3.2", start=5, end=30),
         _span(ref="3.2.2", start=10, end=20),
         _span(ref="a", start=11, end=16)],
        total_lines=40)
    assert [s.ref for s in kept] == ["3", "3.2", "3.2.2", "a"]
    assert [s.parent for s in kept] == [None, "3", "3.2", "3.2.2"]


def test_siblings_after_a_nested_block_are_still_siblings():
    """子项结束之后回到上一层，不能因为「上一条」是个子项就判重叠。"""
    kept, problems = validate(
        [_span(ref="3.2.2", start=10, end=20),
         _span(ref="a", start=11, end=16),
         _span(ref="3.2.3", start=21, end=30)],
        total_lines=40)
    assert [s.ref for s in kept] == ["3.2.2", "a", "3.2.3"]
    assert kept[2].parent is None
    assert problems == []


def test_a_partial_overlap_is_still_an_error():
    """错位相交（10–20 与 15–25）不是嵌套，那是模型划错了边界。"""
    kept, problems = validate(
        [_span(ref="5.1", start=10, end=20), _span(ref="5.2", start=15, end=25)],
        total_lines=40)
    assert [s.ref for s in kept] == ["5.1"]
    assert [p.kind for p in problems] == ["overlap"]


def test_two_spans_on_exactly_the_same_lines_is_an_error():
    """同一段被切了两次。这不是父子，是重复。"""
    kept, problems = validate(
        [_span(ref="5.1", start=10, end=20), _span(ref="5.2", start=10, end=20)],
        total_lines=40)
    assert len(kept) == 1
    assert [p.kind for p in problems] == ["overlap"]


def test_a_child_whose_parent_has_no_ref_stays_topless():
    """父条款原文没编号时，编不出一个上级来。"""
    kept, _ = validate(
        [_span(ref="", start=10, end=30), _span(ref="a", start=11, end=16)],
        total_lines=40)
    assert kept[1].parent is None


# ---------- 父条款只留自己那段 ----------
#
# 不截的话，父条款的正文会把整棵子树包一遍。实测一份国标框架 PDF 里
# 197 条有 160 条受影响：起草时同一段话喂两遍（花两遍钱），
# 自评时同一件事数两遍，导出的 SoA 里一句话出现两次。

def test_a_parent_keeps_only_the_text_before_its_first_child():
    kept, _ = validate(
        [_span(ref="2", start=5, end=20),
         _span(ref="2.1", start=7, end=12),
         _span(ref="2.2", start=13, end=20)],
        total_lines=30)
    assert (kept[0].start, kept[0].end) == (5, 6)      # 只剩引言
    assert (kept[1].start, kept[1].end) == (7, 12)     # 子条款不动
    assert (kept[2].start, kept[2].end) == (13, 20)


def test_a_parent_with_no_intro_ends_up_empty():
    """有些父条款本来就只是个分组标题，下面直接是子条款。空就是空。"""
    kept, _ = validate(
        [_span(ref="2", start=5, end=20), _span(ref="2.1", start=5, end=20)],
        total_lines=30)
    # 区间完全相同会被判重复（见上面那条测试），所以这里错开一行
    kept, _ = validate(
        [_span(ref="2", start=5, end=20), _span(ref="2.1", start=5, end=19)],
        total_lines=30)
    assert kept[0].end < kept[0].start          # 父条款自己没有正文
    assert (kept[1].start, kept[1].end) == (5, 19)


def test_an_empty_parent_slices_to_nothing_not_to_garbage():
    from framework_reader.userframework.outline import slice_lines

    doc = "\n".join(f"第 {n} 行" for n in range(1, 31))
    kept, _ = validate(
        [_span(ref="2", start=5, end=20), _span(ref="2.1", start=5, end=19)],
        total_lines=30)
    assert slice_lines(doc, kept[0].start, kept[0].end) == ""


def test_a_leaf_is_never_trimmed():
    kept, _ = validate([_span(ref="5.1", start=3, end=9)], total_lines=30)
    assert (kept[0].start, kept[0].end) == (3, 9)


def test_trimming_goes_all_the_way_down_the_tree():
    kept, _ = validate(
        [_span(ref="3", start=1, end=40),
         _span(ref="3.2", start=5, end=30),
         _span(ref="3.2.2", start=10, end=20),
         _span(ref="a", start=12, end=16)],
        total_lines=40)
    assert (kept[0].start, kept[0].end) == (1, 4)      # 3 → 首个后代在 5
    assert (kept[1].start, kept[1].end) == (5, 9)      # 3.2 → 首个后代在 10
    assert (kept[2].start, kept[2].end) == (10, 11)    # 3.2.2 → 首个后代在 12
    assert (kept[3].start, kept[3].end) == (12, 16)    # 叶子不动


def test_text_after_the_last_child_is_reported_as_uncovered():
    """截到第一个子条款之前，父条款尾部的收尾句就没人认领了。
    那不该静默消失——报出来，人自己判断要不要合并回去。"""
    from framework_reader.userframework.outline import uncovered

    kept, _ = validate(
        [_span(ref="2", start=5, end=20), _span(ref="2.1", start=7, end=12)],
        total_lines=20)
    details = " ".join(p.detail for p in uncovered(kept, 20))
    assert "13" in details and "20" in details


# ---------- 父条款不该吃掉子条款的标题行 ----------
#
# 实测（用户导入 NIST AI RMF PDF）：
#   [2] Control Matrix   正文：「GOVERN」      ← 这是它子条款的标题
#   [ ] GOVERN           这一条没有自己的正文
# 提示词说 from 不含**自己**的标题行，但没说不含**子条款**的标题行。
# 截到「第一个子条款的正文之前」，子条款的标题就落进了父条款。

DOC_LINES = [
    "Control Matrix",          # 1  父条款的标题（不属于任何条款）
    "下面是各职能的控制。",       # 2  父条款自己的引言
    "GOVERN",                  # 3  子条款的标题
    "治理相关的控制。",           # 4  子条款的正文
    "IDENTIFY",                # 5
    "识别相关的控制。",           # 6
]


def test_the_childs_heading_line_does_not_land_in_the_parent():
    kept, _ = validate(
        [_span(ref="2", label="Control Matrix", start=2, end=6),
         _span(ref="", label="GOVERN", start=4, end=4),
         _span(ref="", label="IDENTIFY", start=6, end=6)],
        total_lines=6, lines=DOC_LINES)
    # 第一个子条款正文在第 4 行，它的标题「GOVERN」在第 3 行——两行都不要
    assert (kept[0].start, kept[0].end) == (2, 2)


def test_a_line_that_is_not_the_childs_heading_stays_in_the_parent():
    """只剥掉真的是子条款标题那一行，不要顺手多砍一行正文。"""
    lines = ["父标题", "引言一", "引言二", "子标题", "子正文"]
    kept, _ = validate(
        [_span(ref="2", label="父", start=2, end=5),
         _span(ref="2.1", label="子标题", start=5, end=5)],
        total_lines=5, lines=lines)
    assert (kept[0].start, kept[0].end) == (2, 3)      # 引言一、引言二都留着


def test_without_the_text_it_falls_back_to_the_plain_trim():
    """拿不到原文时不猜——少剥一行只是多一行噪声，多剥一行是丢正文。"""
    kept, _ = validate(
        [_span(ref="2", label="父", start=2, end=6),
         _span(ref="", label="GOVERN", start=4, end=4)],
        total_lines=6)
    assert (kept[0].start, kept[0].end) == (2, 3)


def test_a_child_with_no_label_cannot_have_its_heading_matched():
    kept, _ = validate(
        [_span(ref="2", label="父", start=2, end=6),
         _span(ref="", label="", start=4, end=4)],
        total_lines=6, lines=DOC_LINES)
    assert (kept[0].start, kept[0].end) == (2, 3)


def test_an_inline_child_keeps_the_parent_intact():
    """子条款的标题和正文在同一行时，前面那行是父条款的正文，不许剥。"""
    lines = ["父标题", "引言", "第一条  正文就在这一行。"]
    kept, _ = validate(
        [_span(ref="2", label="父", start=2, end=3),
         _span(ref="1", label="", start=3, end=3)],
        total_lines=3, lines=lines)
    assert (kept[0].start, kept[0].end) == (2, 2)
