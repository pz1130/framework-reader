import json

import pytest

from framework_reader.interpret.model import RawAnswer
from framework_reader.interpret.questioner import (
    Q1_TEXT,
    Q2_TEXT,
    QuestionerOutputError,
    adaptive_question,
    fixed_questions,
)
from framework_reader.llm.client import FakeClient

ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员一般第二句就问上次复核是谁签的字"),
]


def test_first_two_questions_are_fixed_and_need_no_model_call():
    questions = fixed_questions()
    assert [q.n for q in questions] == [1, 2]
    assert all(q.kind == "fixed" for q in questions)
    assert questions[0].text == Q1_TEXT
    assert questions[1].text == Q2_TEXT


def test_fixed_questions_are_stable_across_calls():
    """106 条问同样的两句——文案漂了，语料就不可比。"""
    assert fixed_questions() == fixed_questions()


def test_adaptive_question_is_number_three():
    client = FakeClient([json.dumps({"question": "欧洲审计员对这条会更严吗？"}, ensure_ascii=False)])
    q = adaptive_question(
        client, control_id="NIST-CSF-2.0:PR.AA-05", outcome="Access permissions…",
        answers=ANSWERS, model="deepseek-chat",
    )
    assert (q.n, q.kind) == (3, "adaptive")
    assert q.text == "欧洲审计员对这条会更严吗？"


def test_adaptive_question_sees_both_previous_answers():
    """第 3 问的全部价值在于模型读过前两答。W2 spec §1.2"""
    client = FakeClient([json.dumps({"question": "追问"}, ensure_ascii=False)])
    adaptive_question(
        client, control_id="X:1", outcome="o", answers=ANSWERS, model="m"
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "权限矩阵表" in sent
    assert "谁签的字" in sent


def test_empty_question_raises():
    client = FakeClient([json.dumps({"question": "   "}, ensure_ascii=False)])
    with pytest.raises(QuestionerOutputError):
        adaptive_question(client, control_id="X:1", outcome="o", answers=ANSWERS, model="m")


def test_non_json_response_raises():
    with pytest.raises(QuestionerOutputError):
        adaptive_question(
            FakeClient(["就问你地域差异吧"]), control_id="X:1", outcome="o",
            answers=ANSWERS, model="m",
        )


def test_outbound_text_error_propagates():
    """出口红线不得被提问器吞成 QuestionerOutputError。"""
    from framework_reader.llm.guard import OutboundTextError

    class BoomClient:
        def complete(self, system, messages, *, model, max_tokens=4096):
            raise OutboundTextError("受版权原文即将出圈")

    with pytest.raises(OutboundTextError, match="受版权原文"):
        adaptive_question(
            BoomClient(), control_id="X:1", outcome="o", answers=ANSWERS, model="m"
        )


def test_q2_anchors_on_the_authors_own_experience():
    """实测（2026-08-20，DE.CM-01）：「审计员会追问哪几句」被作者当场否掉——

    它预设了存在一套标准问法。106 条要问 106 遍，第一条就卡住，后面只会更烦。
    改为锚在个人经历上：问他自己被问过什么，而不是问「审计员们」会问什么。
    """
    assert "you" in Q2_TEXT
    assert "auditors" in Q2_TEXT


def test_adaptive_prompt_tells_the_model_which_fields_exist():
    """实测：Q3 问出了「还需要哪些措施」——答案天然属于 practice，而抽取器不许碰它，

    于是整条答案被丢掉。模型必须知道只有三个字段可落。
    """
    client = FakeClient([json.dumps({"question": "追问"}, ensure_ascii=False)])
    adaptive_question(client, control_id="X:1", outcome="o", answers=ANSWERS, model="m")
    system = client.calls[0]["system"]
    for field in ("common_myth", "auditor_asks", "regional_note"):
        assert field in system, f"提示词没告诉模型有 {field} 这个落点"
