"""Golden samples: the pipeline's acceptance bar. W2 spec §1.3

Zero AI involvement, handwritten, completed before the pipeline code existed. The pipeline reads this directory and never writes it.
"""
from pathlib import Path

from framework_reader.interpret.model import Interpretation
from framework_reader.interpret.store import InterpretationStore

GOLDEN_ROOT = Path("content/golden")

# Chosen by "do the differentiating fields have substance", not by importance. W2 spec §1.3
GOLDEN_CONTROLS = (
    "NIST-CSF-2.0:GV.SC-07",   # richest regional_note
    "NIST-CSF-2.0:PR.AA-05",   # richest common_myth
    "NIST-CSF-2.0:GV.RM-02",   # deliberately hard: the abstract, non-concrete control
)


def golden_store(root: Path = GOLDEN_ROOT) -> InterpretationStore:
    return InterpretationStore(root)


def load_golden(control_id: str, root: Path = GOLDEN_ROOT) -> Interpretation:
    return golden_store(root).load(control_id)


def few_shot_examples(
    exclude: str | None = None, root: Path = GOLDEN_ROOT
) -> list[Interpretation]:
    """Handwritten golden samples, used as few-shot examples in the drafting prompt.

    The goal is teaching the model **granularity** ("name the three offboarded accounts and the date their
    access was revoked", not "there is a periodic process") - not handing it content to copy.

    `exclude` must be the target control itself: feeding PR.AA-05's handwritten sample to draft PR.AA-05
    is not learning, it is copying the answer - and the resulting quality number means nothing.
    """
    store = golden_store(root)
    out: list[Interpretation] = []
    for control_id in GOLDEN_CONTROLS:
        if control_id == exclude or not store.exists(control_id):
            continue
        out.append(store.load(control_id))
    return out
