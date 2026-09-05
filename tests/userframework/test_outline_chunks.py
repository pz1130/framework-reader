"""塞得下就一次过，塞不下才分块。见 2026-08-25 AI 导入设计 §2.1

**分块路径平时跑不到，所以真出事时它是没被验证过的那条。** 因此这里两条
路径同等对待，且必须覆盖「一条被切在块边界上」。
"""
from framework_reader.userframework.outline import (
    ONE_SHOT_MAX_CHARS, Span, plan_calls, shift,
)


def _big(lines: int = 400, width: int = 200) -> str:
    """一份稳稳超过阈值的文档。"""
    return "\n".join("啊" * width for _ in range(lines))


# ---------- 一次过 ----------

def test_a_small_document_is_one_call():
    text = "\n".join(f"第 {n} 行" for n in range(1, 51))
    assert plan_calls(text) == [(1, 50)]


def test_a_document_exactly_at_the_threshold_is_still_one_call():
    """边界上要往「一次过」倒——一次过切得准，多分一块只是省钱。"""
    text = "\n".join("啊" * 100 for _ in range(ONE_SHOT_MAX_CHARS // 100))
    assert len(plan_calls(text)) == 1


def test_an_empty_document_asks_for_nothing():
    assert plan_calls("") == []


# ---------- 分块 ----------

def test_a_document_over_the_threshold_is_split():
    assert len(plan_calls(_big())) > 1


def test_the_pieces_cover_every_line_exactly_once():
    """漏一行就是漏一条制度，重一行就是重叠——两种都不许。"""
    text = _big()
    covered = []
    for lo, hi in plan_calls(text):
        covered.extend(range(lo, hi + 1))
    assert covered == list(range(1, 401))


def test_the_pieces_are_in_order_and_do_not_jump():
    calls = plan_calls(_big())
    for (_, prev_hi), (next_lo, _) in zip(calls, calls[1:]):
        assert next_lo == prev_hi + 1


def test_no_piece_exceeds_the_threshold():
    text = _big()
    lines = text.splitlines()
    for lo, hi in plan_calls(text):
        assert sum(len(x) for x in lines[lo - 1:hi]) <= ONE_SHOT_MAX_CHARS


def test_a_single_line_longer_than_the_threshold_still_gets_its_own_piece():
    """一行八万字（表格被抽成一行）。切不动它，但不能因此死循环或漏掉它。"""
    text = "啊" * (ONE_SHOT_MAX_CHARS * 2)
    assert plan_calls(text) == [(1, 1)]


def test_a_huge_line_between_normal_ones_does_not_swallow_its_neighbours():
    text = "\n".join(["短的一行", "啊" * (ONE_SHOT_MAX_CHARS * 2), "另一短行"])
    calls = plan_calls(text)
    covered = []
    for lo, hi in calls:
        covered.extend(range(lo, hi + 1))
    assert covered == [1, 2, 3]


# ---------- 块内行号搬进全局坐标 ----------

def test_shift_moves_line_numbers_into_document_coordinates():
    """模型看到的是第二块的第 1 行，那在整份文档里是第 301 行。"""
    spans = [Span(ref="5.1", label="a", parent=None, start=1, end=3)]
    got = shift(spans, offset=300)
    assert (got[0].start, got[0].end) == (301, 303)


def test_shift_leaves_everything_else_alone():
    spans = [Span(ref="5.1", label="账号管理", parent="5", start=1, end=3)]
    got = shift(spans, offset=7)
    assert got[0].ref == "5.1"
    assert got[0].label == "账号管理"
    assert got[0].parent == "5"


def test_shift_by_zero_changes_nothing():
    spans = [Span(ref="5.1", label="a", parent=None, start=1, end=3)]
    assert shift(spans, offset=0) == spans


def test_shift_on_an_empty_list_is_empty():
    assert shift([], offset=99) == []
