"""拿真模型的原样回复跑一遍管线。见 fixtures/README.md

**为什么要这个文件：** 提示词是纯文本，改一个字没有任何东西会红。而
Task 14 实测过，改一个字能让结果从 0/7 变成 7/7——第一版提示词在其余
单元测试里全绿，在真文档上每一条行号都 +1，正文丢首行、多吃下一条标题。

这里钉的是**解析到落库那一段**：真回复进去，七条逐字正确的正文出来。
它管不到提示词本身（那要真调用，不进 CI），但提示词改了之后，
管线这一半的回归有人看着。

固件一个字都不许手改——手改过的「真回复」就不是真回复了。
"""
from pathlib import Path

import pytest

from framework_reader.userframework.outline import (
    parse_outline, slice_lines, uncovered, validate,
)

FIXTURES = Path(__file__).parent / "fixtures"
DOC = (FIXTURES / "acme_policy.txt").read_text(encoding="utf-8")
REPLY = (FIXTURES / "deepseek_acme_reply.json").read_text(encoding="utf-8")

# 人工逐条核对过的答案（2026-08-25）。第 11、12 行是「标题正文同行」的条款，
# 其余五条的标题各占一行、正文在后面。
EXPECTED = [
    ("1", "", 11, 11),
    ("2", "", 12, 12),
    ("3", "账号管理", 15, 17),
    ("4", "权限分配", 19, 20),
    ("5", "权限复核", 22, 23),
    ("6", "日志留存", 26, 27),
    ("7", "监控与告警", 29, 29),
]


def _pipeline():
    spans, problems = parse_outline(REPLY)
    kept, more = validate(spans, total_lines=len(DOC.splitlines()))
    return kept, problems + more


def test_the_real_reply_parses_without_complaint():
    _, problems = parse_outline(REPLY)
    assert problems == []


def test_it_cuts_exactly_seven_clauses():
    kept, _ = _pipeline()
    assert len(kept) == 7


@pytest.mark.parametrize("index,expected", list(enumerate(EXPECTED)))
def test_each_clause_lands_on_the_lines_a_human_checked(index, expected):
    kept, _ = _pipeline()
    span = kept[index]
    assert (span.ref, span.label, span.start, span.end) == expected


def test_every_body_is_verbatim_from_the_document():
    """**地基，拿真数据再钉一次。** 落库的正文逐字等于原文对应行。"""
    lines = DOC.splitlines()
    kept, _ = _pipeline()
    for span in kept:
        body = slice_lines(DOC, span.start, span.end)
        assert body == "\n".join(lines[span.start - 1:span.end])


def test_the_account_clause_reads_the_way_the_policy_reads():
    """挑一条出来看全文——参数化的断言不会告诉你正文长什么样。"""
    kept, _ = _pipeline()
    span = next(s for s in kept if s.label == "账号管理")
    assert slice_lines(DOC, span.start, span.end) == (
        "公司应当为每一名员工分配唯一账号，禁止多人共用同一账号。\n"
        "账号的开通须由用人部门主管提交申请，经信息技术部审批后开通。\n"
        "员工离职当日，信息技术部应当停用其全部账号并留存停用记录。")


def test_no_body_contains_a_neighbouring_clause_heading():
    """第一版提示词的病征就是这个：正文末尾多吃了下一条的标题。

    「第 N 条」出现在正文里，几乎一定是边界划歪了。
    """
    import re

    kept, _ = _pipeline()
    for span in kept[2:]:          # 前两条本身就是「第 N 条」开头的整行条款
        body = slice_lines(DOC, span.start, span.end)
        assert not re.search(r"第[一二三四五六七八九十]+条", body), span.label


def test_the_cover_and_the_table_of_contents_are_reported_as_uncovered():
    """封面加目录是连续十行，属于「值得点名」那一档。"""
    kept, _ = _pipeline()
    details = " ".join(p.detail for p in uncovered(kept, len(DOC.splitlines())))
    assert "Lines 1–10" in details


def test_the_heading_lines_are_folded_not_listed_one_by_one():
    """七条条款会留下七个单行洞。逐条列出来会把真问题淹掉。"""
    kept, _ = _pipeline()
    problems = uncovered(kept, len(DOC.splitlines()))
    assert len(problems) <= 3
    assert any("more spot(s)" in p.detail for p in problems)
