"""把整条管线串起来。见 2026-08-25 AI 导入设计 §2

模型一律注入假的。真实调用只在手工验收时跑，与 `fr llm check` 同一个规矩。
"""
from framework_reader.userframework.outline import (
    ONE_SHOT_MAX_CHARS, outline_document, slice_lines,
)

DOC = """五、账号管理
公司应当为每一名员工分配唯一账号，禁止共用。
离职当日停用。
六、口令策略
口令长度不少于 12 位。"""


class _Fake:
    """假客户端。形状与 `GuardedClient` 一致。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, system, messages, *, model, max_tokens=4096,
                 response_format=None):
        self.seen.append((system, messages[0].content, model, response_format))
        return self.replies.pop(0) if self.replies else "[]"


def _big(lines: int = 400, width: int = 200) -> str:
    return "\n".join("啊" * width for _ in range(lines))


# ---------- 地基 ----------

def test_the_body_that_comes_out_is_the_body_that_went_in():
    """模型只给行号，正文来自原文。这条红了整份设计就白做了。"""
    client = _Fake('[{"ref":"5.1","label":"账号管理","parent":null,'
                   '"from":2,"to":3}]')
    result = outline_document(DOC, client=client, model="m")
    span = result.spans[0]
    assert slice_lines(DOC, span.start, span.end) == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


# ---------- 发出去的是什么 ----------

def test_the_model_sees_line_numbers():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    _, user_text, _, _ = client.seen[0]
    assert "0001| 五、账号管理" in user_text


def test_the_model_is_told_it_is_a_scribe():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    system, _, _, _ = client.seen[0]
    assert "You are a scribe" in system


def test_the_prompt_covers_standards_and_frameworks_not_just_policies():
    """NIST.AI.100-1 导入时 MiniMax 先在思考里说「这不是公司制度」。
    提示词开口就写「公司制度」，等于请它放弃。标准 / 框架同样要切。"""
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    system, _, _, _ = client.seen[0]
    assert "framework" in system
    assert "GOVERN" in system or "编号" in system


def test_the_prompt_tells_the_model_not_to_swallow_a_control_table():
    """两套编号是 NIST.AI.100-1 切成 33 条的根因。提示词必须写明
    GOVERN 1.1 各自成条，不能收进 5.1 一章。"""
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    system, _, _, _ = client.seen[0]
    assert "clause numbers beat section" in system
    assert "GOVERN 1.1" in system


def test_the_prompt_forbids_writing_the_body():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    system, _, _, _ = client.seen[0]
    assert "you only mark boundaries" in system


def test_the_model_name_is_passed_through():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="deepseek-chat")
    assert client.seen[0][2] == "deepseek-chat"


# ---------- 一次过 ----------

def test_a_small_document_costs_one_call():
    client = _Fake("[]")
    assert outline_document(DOC, client=client, model="m").calls == 1
    assert len(client.seen) == 1


def test_uncovered_lines_show_up_as_problems():
    client = _Fake('[{"ref":"5.1","label":"a","parent":null,"from":2,"to":3}]')
    result = outline_document(DOC, client=client, model="m")
    assert "uncovered" in [p.kind for p in result.problems]


def test_a_document_with_no_clauses_at_all_is_not_a_crash():
    """有人传了一份会议纪要。0 条加一句说明，不是异常。"""
    result = outline_document(DOC, client=_Fake("[]"), model="m")
    assert result.spans == []
    assert any(p.kind == "uncovered" for p in result.problems)


# ---------- 分块 ----------

def test_a_span_split_across_two_chunks_lands_in_document_coordinates():
    """分块路径的关键用例：第二块的第 1 行必须换算成全文的行号。"""
    text = _big()
    client = _Fake(*[
        '[{"ref":"%d","label":"a","parent":null,"from":1,"to":2}]' % n
        for n in range(1, 9)
    ])
    result = outline_document(text, client=client, model="m")
    starts = [s.start for s in result.spans]
    assert starts == sorted(starts)
    assert starts[0] == 1
    assert starts[1] > 2          # 第二块的第 1 行不是全文第 1 行


def test_every_chunk_gets_its_own_call():
    text = _big()
    client = _Fake()
    result = outline_document(text, client=client, model="m")
    assert result.calls == len(client.seen) > 1


def test_each_chunk_only_sees_its_own_lines():
    """整份文档都塞进每一次调用，等于分块没起作用（还多花了钱）。"""
    text = _big()
    client = _Fake()
    outline_document(text, client=client, model="m")
    for _, user_text, _, _ in client.seen:
        assert len(user_text) <= ONE_SHOT_MAX_CHARS * 1.2   # 加上行号前缀的余量


def test_a_model_failure_in_one_chunk_does_not_kill_the_rest():
    """一块回了垃圾，其余块的结果还要留下——重跑一整份文档要重花一次钱。"""
    text = _big()
    client = _Fake(
        "我看不懂这段文字",
        '[{"ref":"2","label":"b","parent":null,"from":1,"to":2}]',
    )
    result = outline_document(text, client=client, model="m")
    assert [s.ref for s in result.spans] == ["2"]
    assert any(p.kind == "not_json" for p in result.problems)


def test_an_exception_from_the_client_does_not_kill_the_rest():
    """网络断在第三块上。前两块的结果是花过钱的，不能连坐。"""
    class _Flaky(_Fake):
        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            self.seen.append((system, messages[0].content, model, response_format))
            if len(self.seen) == 1:
                raise TimeoutError("超时")
            return '[{"ref":"x","label":"b","parent":null,"from":1,"to":2}]'

    result = outline_document(_big(), client=_Flaky(), model="m")
    assert result.spans                      # 后面几块的结果留下了
    assert any("did not finish" in p.detail for p in result.problems)


def test_the_failing_chunk_is_named_by_its_line_range():
    """「有一块失败了」没用，用户要知道原文的哪一段没进来。"""
    class _Flaky(_Fake):
        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            self.seen.append((system, messages[0].content, model, response_format))
            raise TimeoutError("超时")

    result = outline_document(_big(), client=_Flaky(), model="m")
    assert any("Lines 1–" in p.detail for p in result.problems)


# ---------- 进度回调 ----------

def test_progress_is_reported_once_per_chunk():
    """一份 200 页的制度分五块跑几分钟。不报进度，页面就只能干等。"""
    seen = []
    text = _big()
    outline_document(text, client=_Fake(), model="m",
                     on_chunk=lambda done, total: seen.append((done, total)))
    assert len(seen) > 1
    assert seen[-1][0] == seen[-1][1]          # 最后一次 done == total


def test_progress_counts_up_and_never_goes_backwards():
    seen = []
    outline_document(_big(), client=_Fake(), model="m",
                     on_chunk=lambda done, total: seen.append(done))
    assert seen == sorted(seen)
    assert seen[0] == 1


def test_a_failing_chunk_still_counts_as_progress():
    """一块失败也是跑完了一块。不然进度条会停在那儿不动。"""
    seen = []

    class _Flaky(_Fake):
        def complete(self, system, messages, *, model, max_tokens=4096):
            raise TimeoutError("超时")

    outline_document(_big(), client=_Flaky(), model="m",
                     on_chunk=lambda done, total: seen.append(done))
    assert seen and seen[-1] == len(seen)


def test_no_callback_is_fine():
    """CLI 那边不关心进度。"""
    assert outline_document(DOC, client=_Fake(), model="m").calls == 1


# ---------- 边界对齐接进管线 ----------

def test_a_systematic_off_by_one_is_corrected_end_to_end():
    """实测两次真跑里有一次每条都 +1。管线要自己把它对回去。"""
    doc = ("第三章\n"
           "3.1 日志留存\n"
           "生产系统的日志留存不少于六个月。\n"
           "3.2 监控与告警\n"
           "关键网段应当部署流量监控。")
    client = _Fake('[{"ref":"3.1","label":"日志留存","parent":null,'
                   '"from":4,"to":4},'
                   ' {"ref":"3.2","label":"监控与告警","parent":null,'
                   '"from":6,"to":6}]')
    result = outline_document(doc, client=client, model="m")
    bodies = [slice_lines(doc, s.start, s.end) for s in result.spans]
    assert bodies[0] == "生产系统的日志留存不少于六个月。"
    assert bodies[1] == "关键网段应当部署流量监控。"


def test_the_correction_shows_up_as_a_problem_not_silently():
    doc = ("3.1 日志留存\n生产系统的日志留存不少于六个月。\n"
           "3.2 监控与告警\n关键网段应当部署流量监控。")
    client = _Fake('[{"ref":"3.1","label":"日志留存","parent":null,'
                   '"from":3,"to":3},'
                   ' {"ref":"3.2","label":"监控与告警","parent":null,'
                   '"from":5,"to":5}]')
    result = outline_document(doc, client=client, model="m")
    assert any(p.kind == "snapped" for p in result.problems)


def test_outline_does_not_force_json_mode_on_providers_that_dont_support_it():
    """minimax 不接受 ``response_format`` 字段，会返回预期外结构——
    NIST.AI.100-1 重导报 TypeError 就是这个坑。透传链路保留（drafting
    路径将来可能用），但 outline 调默认不用，强约束交给提示词。"""
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    assert client.seen[0][3] is None


def test_a_client_exception_detail_lands_in_problems_so_the_next_person_can_see():
    """前一个只报 ``TypeError``、下一个接手的人靠猜——把 ``str(exc)``
    拼进 detail，下次能从 HTTP 错误体里看到具体原因。"""
    class _Boom(_Fake):
        def complete(self, system, messages, *, model, max_tokens=4096,
                     response_format=None):
            raise RuntimeError("厂商返回了预期外的结构：{\"error\": \"bad json mode\"}")

    result = outline_document(_big(), client=_Boom(), model="m")
    not_json = [p for p in result.problems if p.kind == "not_json"]
    assert not_json, "异常该被记成 not_json problem"
    assert "预期外的结构" in not_json[0].detail
    assert "bad json mode" in not_json[0].detail
