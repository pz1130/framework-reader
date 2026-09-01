"""提问器。W2 spec §2.2

Q1/Q2 是常量，不调模型；只有 Q3 自适应，在作者已说了两轮之后发出。
"""
from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.model import Question, RawAnswer
from framework_reader.llm.client import LLMClient, Message
from framework_reader.llm.guard import OutboundTextError
from framework_reader.prompts import load_prompt

Q1_TEXT = "What is the most common misunderstanding about this control?"
# 不问「审计员们会问什么」——那预设了一套标准问法，作者会当场反驳「每个人问的都不一样」。
# 锚在他自己被问过的经历上。实测（2026-08-20，DE.CM-01）第一条就卡在这里。
Q2_TEXT = "Of the times you have been questioned yourself, where do auditors probe hardest? One sentence is enough."


class QuestionerOutputError(Exception):
    """模型没给出可用的问题。"""


def fixed_questions() -> list[Question]:
    return [
        Question(n=1, kind="fixed", text=Q1_TEXT),
        Question(n=2, kind="fixed", text=Q2_TEXT),
    ]


def adaptive_question(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    answers: list[RawAnswer],
    model: str,
) -> Question:
    transcript = "\n".join(f"[Answer {a.n}] {a.text}" for a in sorted(answers, key=lambda a: a.n))
    user = f"Control: {control_id}\nFramework text: {outcome}\n\nTheir answers:\n{transcript}"
    try:
        data = parse_json_object(
            client.complete(
                load_prompt("questioner"), [Message(role="user", content=user)], model=model
            )
        )
    except OutboundTextError:
        raise
    except Exception as exc:
        raise QuestionerOutputError(f"questioner output unusable: {exc}") from exc

    text = str(data.get("question") or "").strip()
    if not text:
        raise QuestionerOutputError("questioner returned an empty question")
    return Question(n=3, kind="adaptive", text=text)
