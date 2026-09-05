"""模型输出的解析。见 2026-08-25 AI 导入设计 §1.2、§2.2

模型的输出是**不可信输入**。它会裹 markdown 代码围栏、会多说一句话、
会把数字写成字符串、会漏字段、会自作主张塞一个 body 进来。这里一条都不信。
"""
from framework_reader.userframework.outline import parse_outline

GOOD = '[{"ref":"5.1","label":"账号管理","parent":null,"from":13,"to":14}]'


# ---------- 认得出来的 ----------

def test_a_clean_array_parses():
    spans, problems = parse_outline(GOOD)
    assert problems == []
    assert len(spans) == 1
    assert spans[0].ref == "5.1"
    assert spans[0].label == "账号管理"
    assert spans[0].parent is None
    assert (spans[0].start, spans[0].end) == (13, 14)


def test_a_markdown_fence_is_peeled_off():
    """提示词说了「只输出 JSON」，模型照样会裹 ```json。为这个作废一整块不值。"""
    spans, problems = parse_outline("```json\n" + GOOD + "\n```")
    assert problems == []
    assert len(spans) == 1


def test_a_bare_fence_without_a_language_is_peeled_too():
    spans, problems = parse_outline("```\n" + GOOD + "\n```")
    assert problems == []
    assert len(spans) == 1


def test_string_line_numbers_are_accepted():
    """模型常把数字写成字符串。为这个丢掉一条正确的边界不值。"""
    spans, _ = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":"13","to":"14"}]')
    assert (spans[0].start, spans[0].end) == (13, 14)


def test_an_empty_parent_string_becomes_none():
    spans, _ = parse_outline(
        '[{"ref":"5.1","label":"a","parent":"","from":1,"to":2}]')
    assert spans[0].parent is None


def test_surrounding_whitespace_and_newlines_are_fine():
    spans, problems = parse_outline("\n\n  " + GOOD + "  \n")
    assert problems == []
    assert len(spans) == 1


def test_an_empty_array_is_not_an_error():
    """这一段确实没有条款（目录页、附录）。空不是错。"""
    spans, problems = parse_outline("[]")
    assert spans == []
    assert problems == []


# ---------- 整块作废的 ----------

def test_a_body_key_voids_the_whole_block():
    """正文只能来自原文。模型吐了 body 说明它没在按契约干活——
    这一块的其余部分也不能信。"""
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"x","parent":null,"from":1,"to":2,'
        '"body":"我编的正文"}]')
    assert spans == []
    assert [p.kind for p in problems] == ["has_body"]


def test_a_body_key_on_a_later_entry_voids_it_too():
    """前面几条看着正常也不行。它已经证明自己没在按契约干活。"""
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":1,"to":2},'
        ' {"ref":"5.2","label":"b","parent":null,"from":3,"to":4,"body":"x"}]')
    assert spans == []
    assert [p.kind for p in problems] == ["has_body"]


def test_a_truncated_array_keeps_the_objects_already_written():
    """max_tokens 用光时数组没有闭合 ``]``。前面写完的对象是好的，
    整段作废会把 Part 1 几百行变成「没切出条款」——NIST.AI.100-1 就是这样。"""
    raw = (
        '[{"ref":"1","label":"Framing Risk","parent":null,"from":195,"to":232},'
        ' {"ref":"1.1","label":"Harms","parent":"1","from":200,"to":232},'
        ' {"ref":"1.2","label":"Challenges","parent":"1","from":233,"to":'
    )
    spans, problems = parse_outline(raw)
    assert [s.ref for s in spans] == ["1", "1.1"]
    assert any("truncated" in p.detail for p in problems)


def test_a_truncated_array_with_a_trailing_comma_still_salvages():
    raw = (
        '[{"ref":"5.1","label":"a","parent":null,"from":1,"to":2},'
        ' {"ref":"5.2","label":"b","parent":null,"from":3,"to":4},'
    )
    spans, problems = parse_outline(raw)
    assert [s.ref for s in spans] == ["5.1", "5.2"]
    assert any("truncated" in p.detail for p in problems)


def test_a_complete_array_is_not_reported_as_truncated():
    spans, problems = parse_outline(GOOD)
    assert problems == []
    assert spans[0].ref == "5.1"


def test_concatenated_objects_without_an_array_are_salvaged():
    """MiniMax 有时不包 ``[...]``，一条一个对象、换行拼在一起。
    最后一条还常被截断。前面写完的要保住。"""
    raw = (
        '{"ref":"1","label":"Framing Risk","parent":null,"from":195,"to":228}\n'
        '{"ref":"1.1","label":"Harms","parent":"1","from":200,"to":228}\n'
        '{"ref":"1.2","ref_from":"or'
    )
    spans, problems = parse_outline(raw)
    assert [s.ref for s in spans] == ["1", "1.1"]
    assert any("truncated" in p.detail for p in problems)


def test_complete_concatenated_objects_are_not_called_truncated():
    raw = (
        '{"ref":"5.1","label":"a","parent":null,"from":1,"to":2}\n'
        '{"ref":"5.2","label":"b","parent":null,"from":3,"to":4}\n'
    )
    spans, problems = parse_outline(raw)
    assert [s.ref for s in spans] == ["5.1", "5.2"]
    assert not any("截断" in p.detail for p in problems)


def test_not_json_at_all_voids_the_block():
    spans, problems = parse_outline("这份文档看起来是一份会议纪要。")
    assert spans == []
    assert [p.kind for p in problems] == ["not_json"]


def test_an_object_instead_of_an_array_voids_the_block():
    spans, problems = parse_outline('{"ref":"5.1"}')
    assert spans == []
    assert [p.kind for p in problems] == ["not_json"]


def test_an_empty_reply_voids_the_block():
    """模型什么都没回。这不是「这段没有条款」，是这次调用没成。"""
    spans, problems = parse_outline("")
    assert spans == []
    assert [p.kind for p in problems] == ["not_json"]


# ---------- 只丢一条的 ----------

def test_an_entry_missing_from_or_to_is_dropped_not_fatal():
    """一条缺字段不该带走整块。丢它，并说出丢了哪一条。"""
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":1,"to":2},'
        ' {"ref":"5.2","label":"b","parent":null}]')
    assert [s.ref for s in spans] == ["5.1"]
    assert [p.kind for p in problems] == ["not_json"]
    assert "5.2" in problems[0].detail


def test_a_non_numeric_line_number_drops_just_that_entry():
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":"一","to":"二"},'
        ' {"ref":"5.2","label":"b","parent":null,"from":3,"to":4}]')
    assert [s.ref for s in spans] == ["5.2"]
    assert [p.kind for p in problems] == ["not_json"]


def test_an_item_that_is_not_an_object_drops_just_that_item():
    spans, problems = parse_outline(
        '["一句话", {"ref":"5.2","label":"b","parent":null,"from":3,"to":4}]')
    assert [s.ref for s in spans] == ["5.2"]
    assert [p.kind for p in problems] == ["not_json"]


def test_a_dropped_entry_with_no_ref_is_still_named_somehow():
    """报错要能指认是哪一条，不然用户对不上原文。"""
    _, problems = parse_outline(
        '[{"ref":"","label":"账号申请与审批","parent":null}]')
    assert "账号申请与审批" in problems[0].detail


# ---------- 现实里模型不守约的几种包装 ----------

def test_a_preamble_plus_array_plus_afterword_still_parses():
    """NIST.AI.100-1 失败那次正是这种：模型先客套、再粘 JSON、再加问候。
    _extract_json_array 应该从第一个 ``[`` 到最后一个 ``]`` 抠出来。"""
    raw = ("好的，下面是切分结果：\n\n"
           + GOOD + "\n\n如有需要请告诉我。")
    spans, problems = parse_outline(raw)
    assert problems == []
    assert len(spans) == 1
    assert spans[0].ref == "5.1"


def test_a_json_fence_around_the_array_still_parses():
    """即使示例去掉了围栏，模型可能还是套一下；find/rfind 仍然能找到。"""
    raw = "```json\n" + GOOD + "\n```"
    spans, problems = parse_outline(raw)
    assert problems == []
    assert len(spans) == 1


def test_prose_with_no_json_returns_not_json():
    """真没结构就老实说，不要凭空猜。"""
    _, problems = parse_outline(
        "我没有看到可识别的条款标题与编号，这份文档看起来是会议纪要。")
    assert [p.kind for p in problems] == ["not_json"]


def test_not_json_problem_includes_the_raw_response_so_the_next_person_can_see():
    """之前 failure 只说「不是 JSON」，下次接手的人靠猜。这次取个证：
    原始回复的头 300 字会进 problems detail，UI 里能看到模型到底回了什么。"""
    raw = ("好的，下面是切分结果：\n\n"
           "我尝试了，但是这份文档的结构我没看懂。"
           "它看起来像是表格而不是条款。\n\n如有需要请告诉我。")
    _, problems = parse_outline(raw)
    assert len(problems) == 1
    assert problems[0].kind == "not_json"
    assert "What it actually replied" in problems[0].detail
    assert "我尝试了" in problems[0].detail
    # 换行被替成 ⏎，方便单行显示
    assert "⏎" in problems[0].detail


def test_raw_is_truncated_at_300_chars():
    raw = "好的。" + "啊" * 1000
    _, problems = parse_outline(raw)
    # detail 末尾有 … 表示截断
    assert "..." in problems[0].detail


# ---------- 真实模型行为：think 标签、字符串里的方括号、嵌套数组 ----------

def test_think_tags_get_stripped_before_array_extraction():
    """minimax/MiniMax-M3 总是先把思考塞进 ``<think>...</think>`` 里，
    思考文本里有方括号（举例、列表）。之前 find/rfind 会被这些方括号
    错配，新解析器先剥 think 标签再平衡配对就稳了。"""
    raw = (
        "<think>The user wants me to split a document. Let me analyze.\n"
        "Here are sections [Section A], [Section B] I see...\n"
        "Plan: I'll output JSON with ref/from/to.</think>\n"
        + GOOD
    )
    spans, problems = parse_outline(raw)
    assert problems == [], problems
    assert spans[0].ref == "5.1"


def test_brackets_inside_strings_do_not_break_balance():
    """某条 label 是 ``[草稿]``，字符串里出现方括号不影响配对。"""
    raw = '[{"ref":"1","label":"[草稿] 标题","parent":null,"from":1,"to":2}]'
    spans, problems = parse_outline(raw)
    assert problems == []
    assert spans[0].label == "[草稿] 标题"


def test_brackets_inside_think_do_not_pull_the_pairing_off():
    """think 里 ``[Section A], [Section B]`` 也不会干扰 outermost 配对。"""
    raw = (
        "<think>I see [Section A] and [Section B] here</think>"
        + '[{"ref":"1","label":"a","parent":null,"from":1,"to":2}]'
    )
    spans, problems = parse_outline(raw)
    assert problems == []
    assert spans[0].ref == "1"


def test_think_with_no_real_array_returns_not_json():
    """模型只思考没输出 JSON——应该 not_json，detail 显示剥 think 后的 raw。"""
    raw = (
        "<think>This document is hard to parse. "
        "Let me think carefully.</think>"
        "I cannot produce a JSON array for this document."
    )
    _, problems = parse_outline(raw)
    assert [p.kind for p in problems] == ["not_json"]
    # 剥 think 之后只剩真正的输出
    assert "I cannot produce" in problems[0].detail
    # think 里的内容不应该出现在 detail 里
    assert "think carefully" not in problems[0].detail


def test_raw_for_debug_strips_think_tags():
    """problems detail 也要剥 think——不然用户看到的是思考不是输出。"""
    from framework_reader.userframework.outline import _raw_for_debug
    raw = "<think>thinking details here</think>[1,2,3]"
    out = _raw_for_debug(raw)
    assert "thinking" not in out
    assert "[1,2,3]" in out


def test_json_inside_closed_think_is_still_recovered():
    """MiniMax-M3 常把答案写在思考里、闭合标签后再也没吐 JSON。
    剥 think 之后 spoken 是空的——要回过头从思考里把数组抠出来，
    否则 NIST.AI.100-1 这种长文档永远切出 0 条。"""
    raw = (
        "<think>The user wants clauses. I see [GOVERN] and [MAP]. "
        "Output: " + GOOD + "</think>"
    )
    spans, problems = parse_outline(raw)
    assert problems == [], problems
    assert spans[0].ref == "5.1"


def test_json_inside_unclosed_think_is_still_recovered():
    """思考把 max_tokens 用光、``</think>`` 永远出不来。probe.py 已经
    按这个剥；outline 解析器早先只认闭合标签，未闭合的思考里就算有
    完整数组也当 not_json。"""
    raw = (
        "<think>Let me analyze this NIST document. Sections include "
        "[Section A] and [Section B].\n" + GOOD
    )
    spans, problems = parse_outline(raw)
    assert problems == [], problems
    assert spans[0].ref == "5.1"


def test_brackets_in_think_do_not_beat_the_real_array_after_it():
    """思考里先出现 ``[GOVERN]`` 这种废括号，真数组在闭合之后。
    要跳过解析失败的 ``[``，不能把第一对当成结果。"""
    raw = (
        "<think>Functions are [GOVERN], [MAP], [MEASURE].</think>\n"
        + GOOD
    )
    spans, problems = parse_outline(raw)
    assert problems == [], problems
    assert spans[0].ref == "5.1"


def test_unclosed_think_with_no_array_still_not_json():
    """没闭合、也没有数组：就是这次调用没成。detail 不该把半篇思考
    端给用户（probe 同款：没闭合 = 后面没有正文）。"""
    raw = "<think>The user wants me to split a document into clauses/clauses."
    _, problems = parse_outline(raw)
    assert [p.kind for p in problems] == ["not_json"]
    assert "split a document" not in problems[0].detail
