"""Rendering and leak detection for the three variants. spec §4

Every character the judges see leaves through here. Any word that could expose "which
one is the product" is blocked here, without exception.
"""
from framework_reader.interpret.render import (  # noqa: F401  re-export, do not remove
    MappingRef,
    render_interpretation as render_product,
    render_mappings,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

# Presence of any of these leaks the origin. Control ids are not on the list — all three
# variants share them, and the judges need them.
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


# The question a colleague would fire off casually. spec §1: plain, not tuned.
_BARE_QUESTION = (
    "{control_id} - what does this control require? I have to prepare audit materials for it next week.\n"
    "The framework text is: {outcome}"
)


def render_bare(
    client: LLMClient, *, control_id: str, outcome: str, model: str
) -> str:
    """Variant (b): the same model as the drafter, a plain prompt, no framework
    grounding, no structural requirements."""
    question = _BARE_QUESTION.format(control_id=control_id, outcome=outcome)
    return client.complete(
        load_prompt("bare_llm"),
        [Message(role="user", content=question)],
        model=model,
    ).strip()
