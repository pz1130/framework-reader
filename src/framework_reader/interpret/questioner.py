"""The questioner. W2 spec §2.2

Q1/Q2 are constants, no model call; only Q3 adapts, asked after the author has already spoken twice.
"""
from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.model import Question, RawAnswer
from framework_reader.llm.client import LLMClient, Message
from framework_reader.llm.guard import OutboundTextError
from framework_reader.prompts import load_prompt

Q1_TEXT = "What is the most common misunderstanding about this control?"
# Never ask "what would auditors ask" - it presumes a standard set of questions the author would push back on.
# Anchor in their own experience of being asked. Tested 2026-08-20 on DE.CM-01: this exact spot stalled.
Q2_TEXT = "Of the times you have been questioned yourself, where do auditors probe hardest? One sentence is enough."


class QuestionerOutputError(Exception):
    """The model returned no usable question."""


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
