"""行号快照与逐字截取。见 2026-08-25 AI 导入设计 §1

**这个文件里的第一条测试是整份设计的地基。** 模型只划边界，正文由代码从
原文按行号截——所以「截出来的字逐字等于原文」这件事一旦不成立，
整个方案就退化成「让模型改写你的制度」，而后面起草的解读、自评的证据、
差距报告全都基于那段正文。
"""
from framework_reader.userframework.outline import (
    line_count, numbered, slice_lines,
)

DOC = """五、账号管理
公司应当为每一名员工分配唯一账号，禁止共用。
离职当日停用。
六、口令策略
口令长度不少于 12 位。"""


def test_the_text_we_store_is_the_text_from_the_document():
    """地基。这条红了不许往下走。"""
    assert slice_lines(DOC, 2, 3) == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_line_numbers_are_one_based_and_inclusive():
    assert slice_lines(DOC, 1, 1) == "五、账号管理"
    assert slice_lines(DOC, 5, 5) == "口令长度不少于 12 位。"


def test_whitespace_inside_a_line_is_not_touched():
    """制度里的缩进和全角空格是原样，不是格式噪声——「不逐字」从这里开始。"""
    text = "一、总则\n　　本办法适用于  全体员工。\n二、附则"
    assert slice_lines(text, 2, 2) == "　　本办法适用于  全体员工。"


def test_numbering_puts_a_width_four_number_in_front():
    """模型要会数行号。宽度固定、右对齐、竖线分隔，比裸数字好认。"""
    assert numbered(DOC).splitlines()[0] == "0001| 五、账号管理"
    assert numbered(DOC).splitlines()[4] == "0005| 口令长度不少于 12 位。"


def test_numbering_does_not_change_the_text_itself():
    """加行号只是给模型看的那一份。原文快照存的是没加过的。"""
    stripped = "\n".join(
        line.split("| ", 1)[1] for line in numbered(DOC).splitlines())
    assert stripped == DOC


def test_line_count_matches_what_numbering_produced():
    assert line_count(DOC) == 5
    assert len(numbered(DOC).splitlines()) == 5


def test_an_out_of_range_slice_clamps_instead_of_raising():
    """越界由校验层挡（Task 5）。真漏到这儿也不该炸——炸掉的是后台线程，
    而用户看到的是一个永远停在「切分中」的页面。"""
    assert slice_lines(DOC, 4, 99) == "六、口令策略\n口令长度不少于 12 位。"
    assert slice_lines(DOC, 0, 1) == "五、账号管理"
    assert slice_lines(DOC, 99, 120) == ""


def test_an_inverted_range_is_empty_not_reversed():
    assert slice_lines(DOC, 4, 2) == ""


def test_an_empty_document_has_no_lines():
    assert line_count("") == 0
    assert numbered("") == ""
    assert slice_lines("", 1, 3) == ""
