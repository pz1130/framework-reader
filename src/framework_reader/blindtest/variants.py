"""三份变体的渲染与泄露检测。spec §4

评委看到的每一个字都从这里出去。凡是能暴露「哪份是产品」的词，一律拦在这里。
"""
from framework_reader.interpret.render import (  # noqa: F401  转出口，勿删
    MappingRef,
    render_interpretation as render_product,
    render_mappings,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

# 出现即泄露来源。控制编号不在此列——三份变体共享它，评委也需要它。
LEAK_WORDS = (
    "interpretation",
    "basis",
    "provenance",
    "inferred",
    "practitioner",
    "framework_reader",
    "drafter",
    "prompt_version",
    "draft",
    "confirmed",
)

def leak_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in LEAK_WORDS if word in lowered]


def render_original(outcome: str) -> str:
    return outcome.strip()


# 同行随手会问的那句话。spec §1：朴素，不调优。
_BARE_QUESTION = (
    "{control_id} - what does this control require? I have to prepare audit materials for it next week.\n"
    "The framework text is: {outcome}"
)


def render_bare(
    client: LLMClient, *, control_id: str, outcome: str, model: str
) -> str:
    """变体 (b)：与起草器同一个模型，朴素提示词，无框架接地、无结构要求。"""
    question = _BARE_QUESTION.format(control_id=control_id, outcome=outcome)
    return client.complete(
        load_prompt("bare_llm"),
        [Message(role="user", content=question)],
        model=model,
    ).strip()

