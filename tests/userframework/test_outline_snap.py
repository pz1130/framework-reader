"""拿原文里的标题行把边界对回去。

模型算行号会系统性地差一行——实测同一份文档同一个提示词，两次跑一次全对、
一次每条都 +1。提示词压得住一时，压不住每一次。

但这件事**代码能自己查**：模型给了 `ref="3.1"`，那「3.1」那一行在原文里的
位置是确定的，正文就该从它的下一行开始。和核对编号来源同一个思路——
**不信模型的自述，拿原文核对。**

只在**多数条款给出同一个偏移**时才整体挪。个别条款对不上是切歪了，
那是另一回事，不该拿它去挪所有人。
"""
from framework_reader.userframework.outline import Span, snap_to_headings

LINES = [
    "ACME 信息安全管理办法",                                  # 1
    "第三章  日志与监控",                                     # 2
    "3.1 日志留存",                                          # 3
    "生产系统的登录日志留存期限不少于六个月。",                    # 4
    "日志存储介质应当具备防篡改能力。",                          # 5
    "3.2 监控与告警",                                        # 6
    "关键网段与边界设备应当部署流量监控。",                       # 7
    "告警须有专人跟进并形成处理记录。",                          # 8
]


def _span(ref, label, start, end, **kw):
    return Span(ref=ref, label=label, parent=None, start=start, end=end, **kw)


def test_a_systematic_off_by_one_is_snapped_back():
    """实测的那个：每一条都晚一行。"""
    spans, problems = snap_to_headings(
        [_span("3.1", "日志留存", 5, 6), _span("3.2", "监控与告警", 8, 8)], LINES)
    assert [(s.start, s.end) for s in spans] == [(4, 5), (7, 7)]
    assert problems and "aligned to the headings" in problems[0].detail


def test_correct_boundaries_are_left_alone():
    original = [_span("3.1", "日志留存", 4, 5), _span("3.2", "监控与告警", 7, 8)]
    spans, problems = snap_to_headings(original, LINES)
    assert spans == original
    assert problems == []


def test_one_clause_disagreeing_does_not_move_everyone():
    """个别条款切歪了是它自己的事，不该拿它去挪所有人。"""
    spans, _ = snap_to_headings(
        [_span("3.1", "日志留存", 4, 5), _span("3.2", "监控与告警", 7, 8),
         _span("x", "查无此条", 2, 2)], LINES)
    assert [(s.start, s.end) for s in spans][:2] == [(4, 5), (7, 8)]


def test_a_clause_whose_heading_is_nowhere_is_ignored_not_guessed():
    spans, _ = snap_to_headings([_span("9.9", "查无此条", 3, 4)], LINES)
    assert (spans[0].start, spans[0].end) == (3, 4)


def test_an_inline_clause_counts_its_own_line_as_the_body():
    """编号和正文在同一行时，正文就从那一行本身开始，不是下一行。"""
    lines = ["总则",
             "1.1 为规范公司信息系统的安全管理，制定本办法。",
             "1.2 本办法适用于全体员工及第三方人员。"]
    spans, _ = snap_to_headings(
        [_span("1.1", "", 3, 3), _span("1.2", "", 4, 4)], lines)
    assert (spans[0].start, spans[0].end) == (2, 2)


def test_a_chinese_numeral_heading_cannot_be_matched_and_is_left_alone():
    """「第一条」里没有字面的「1」。找不到就是找不到——**不猜**。

    这类条款不参与偏移投票，但只要文档里还有别的条款对得上（`3.1` 这种），
    整体偏移照样能算出来，它们也跟着挪。
    """
    lines = ["总则", "第一条  为规范管理，制定本办法。"]
    spans, problems = snap_to_headings([_span("1", "", 3, 3)], lines)
    assert (spans[0].start, spans[0].end) == (3, 3)
    assert problems == []


def test_the_label_is_used_when_there_is_no_ref():
    lines = ["日志留存", "生产系统的日志留存不少于六个月。"]
    spans, _ = snap_to_headings([_span("", "日志留存", 3, 3)], lines)
    assert (spans[0].start, spans[0].end) == (2, 2)


def test_shifting_never_pushes_a_span_out_of_the_document():
    """整体挪之后越界，那说明这个偏移是假的，宁可不挪。"""
    spans, _ = snap_to_headings([_span("3.1", "日志留存", 1, 2)], LINES)
    assert spans[0].start >= 1


def test_the_correction_is_reported_not_silent():
    """悄悄挪了一行，用户永远不知道我们动过他的边界。"""
    _, problems = snap_to_headings(
        [_span("3.1", "日志留存", 5, 6), _span("3.2", "监控与告警", 8, 8)], LINES)
    assert problems[0].kind == "snapped"
    assert "1" in problems[0].detail


def test_no_spans_is_not_a_crash():
    assert snap_to_headings([], LINES) == ([], [])
