"""原文没有编号或标题时，AI 补上——但要标出来是它补的。

**这条线要划清楚：**
- **正文**不能由 AI 生成。它是用户制度的原话，起草的解读、自评的证据全基于它。
- **编号和标题**能。它们是编目用的标识，不是制度的内容。留空的代价是
  这条条款根本存不进库（`user_control.id` 是主键），等于把我们的问题
  推给用户，而他不会改。

补出来的必须标成 `derived`——「谁写的要能看出来」是这个产品的地基，
和条款页那套「AI 初稿」一个规矩。
"""
from framework_reader.userframework.outline import Span, fill_gaps

LINES = [
    "3.2 日志与监控",
    "生产系统的登录日志、操作日志留存期限不少于六个月。",
    "日志存储介质应当具备防篡改能力，删除须经安全负责人批准。",
]


def _span(ref="", label="", parent=None, start=2, end=2, **kw):
    return Span(ref=ref, label=label, parent=parent, start=start, end=end, **kw)


# ---------- 编号 ----------

def test_a_child_with_no_ref_follows_its_parents_numbering():
    got = fill_gaps([
        _span(ref="3.2", label="日志与监控", start=1, end=1),
        _span(parent="3.2", start=2, end=2),
        _span(parent="3.2", start=3, end=3),
    ], LINES)
    assert [s.ref for s in got] == ["3.2", "3.2.1", "3.2.2"]


def test_a_generated_ref_is_marked_as_derived():
    got = fill_gaps([_span(parent="3.2", start=2, end=2)], LINES)
    assert got[0].ref_from == "derived"


def test_a_ref_that_came_from_the_document_is_left_alone():
    """用一个**原文里真有**的编号——早先这里写的是 3.2.9，而它压根不在
    文档里，于是这条测试测的不是「不覆盖原文的编号」，是「不覆盖任何编号」。"""
    got = fill_gaps([_span(ref="3.2", label="日志与监控", start=1, end=1)], LINES)
    assert got[0].ref == "3.2"
    assert got[0].ref_from == "original"


def test_a_generated_ref_never_collides_with_a_real_one():
    """原文里已经有 3.2.1 了，补出来的不能是它。"""
    got = fill_gaps([
        _span(ref="3.2", label="a", start=1, end=1),
        _span(ref="3.2.1", label="b", parent="3.2", start=2, end=2),
        _span(parent="3.2", start=3, end=3),
    ], LINES)
    assert got[2].ref == "3.2.2"


def test_two_generated_refs_do_not_collide_with_each_other():
    got = fill_gaps([_span(parent="3.2", start=2, end=2),
                     _span(parent="3.2", start=3, end=3)], LINES)
    assert got[0].ref != got[1].ref


def test_a_top_level_span_with_no_parent_gets_a_plain_number():
    got = fill_gaps([_span(start=2, end=2)], LINES)
    assert got[0].ref == "1"


def test_a_parent_that_itself_has_no_ref_does_not_produce_a_dot_prefix():
    """父条款自己也是补的，子条款不能变成「.1」。"""
    got = fill_gaps([
        _span(start=1, end=1),
        _span(parent="", start=2, end=2),
    ], LINES)
    assert not any(s.ref.startswith(".") for s in got)
    assert all(s.ref for s in got)


# ---------- 标题 ----------

def test_a_missing_label_is_derived_from_the_body():
    got = fill_gaps([_span(parent="3.2", start=2, end=2)], LINES)
    assert got[0].label
    assert got[0].label_from == "derived"


def test_a_derived_label_is_short_enough_to_be_a_title():
    got = fill_gaps([_span(parent="3.2", start=2, end=2)], LINES)
    assert len(got[0].label) <= 24


def test_a_derived_label_stops_at_the_first_full_stop():
    got = fill_gaps([_span(parent="3.2", start=2, end=2)], LINES)
    assert "。" not in got[0].label


def test_a_label_that_came_from_the_document_is_left_alone():
    got = fill_gaps([_span(ref="3.2", label="日志与监控", start=1, end=1)], LINES)
    assert got[0].label == "日志与监控"
    assert got[0].label_from == "original"


def test_a_clause_with_no_body_still_gets_a_label():
    """只当分组标题的父条款截完是空的，但它照样要有个名字。"""
    got = fill_gaps([_span(ref="3.2", start=5, end=4)], LINES)
    assert got[0].label


# ---------- 不许碰的东西 ----------

def test_the_line_range_is_never_touched():
    """补标识不许动边界——正文是按行号截的。"""
    got = fill_gaps([_span(parent="3.2", start=2, end=3)], LINES)
    assert (got[0].start, got[0].end) == (2, 3)


def test_the_parent_link_is_never_touched():
    got = fill_gaps([_span(parent="3.2", start=2, end=2)], LINES)
    assert got[0].parent == "3.2"


def test_nothing_to_fill_changes_nothing():
    spans = [_span(ref="3.2", label="日志与监控", start=1, end=1)]
    assert fill_gaps(spans, LINES) == spans


# ---------- 不信模型的自述，拿原文核对 ----------
#
# 实测：模型把两行没编号的正文合成一条，给了 ref="4"、label="施行与解释"，
# 却没填 ref_from。默认按「有值即原文的」算，结果**它编的编号被记成了
# 原文里就有的**——这正是这个产品最不能出的错。
#
# 模型的自述和它的输出一样不可信。原文里有没有这几个字，代码自己会查。

def test_a_ref_the_model_invented_is_caught_even_if_it_claims_otherwise():
    got = fill_gaps([_span(ref="4", label="施行与解释", start=2, end=3,
                           ref_from="original", label_from="original")], LINES)
    assert got[0].ref_from == "derived"
    assert got[0].label_from == "derived"


def test_a_ref_that_really_is_in_the_document_stays_original():
    got = fill_gaps([_span(ref="3.2", label="日志与监控", start=2, end=2)], LINES)
    assert got[0].ref_from == "original"
    assert got[0].label_from == "original"


def test_the_heading_line_just_above_counts_as_the_document():
    """条款的编号和标题在它正文的**上一行**——那是提示词要求的。"""
    lines = ["3.5 访问控制", "只有授权人员可以进入机房。"]
    got = fill_gaps([_span(ref="3.5", label="访问控制", start=2, end=2)], lines)
    assert got[0].ref_from == "original"


def test_a_label_the_model_polished_is_marked_derived():
    """原文写「日志与监控」，模型写「日志与监控管理」——那不是抄的。"""
    got = fill_gaps([_span(ref="3.2", label="日志与监控管理",
                           start=2, end=2)], LINES)
    assert got[0].label_from == "derived"


def test_a_clause_with_no_body_falls_back_to_the_models_word():
    """截成空的分组标题没有正文可核对。这时候只能信它说的。"""
    got = fill_gaps([_span(ref="3.2", label="日志与监控", start=5, end=4,
                           ref_from="original")], LINES)
    assert got[0].ref_from == "original"
