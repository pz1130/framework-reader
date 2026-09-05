"""An approximation of extraction fidelity. W2 spec §2.3

It catches wholesale invention, not synonym-swapping. The real defence is signing each field in $EDITOR.
Deliberately not a build assertion - the false-positive rate would ruin the feel.
"""
import re

from framework_reader.interpret.model import DIFFERENTIATING_FIELDS, Field, RawAnswer

# Regulation clause numbers, percentages, years - the prompts forbid the AI from inventing them,
# but a ban cannot stop the model, and nobody can hand-check 106 controls. Flag every hit for human verification.
# Control numbers (A.5.22 / AC-2 / DE.CM-01) are exempt - they are this product's own identifiers.
_CITATION_RE = re.compile(
    r"第\s*\d+\s*条"          # Chinese "article N" clause pattern
    r"|Art(?:icle)?\.?\s*\d+"  # Article 32 / Art. 32
    r"|§\s*\d+"                # §32
    r"|\d+(?:\.\d+)?\s*%"     # 95%
    r"|(?:19|20)\d{2}\s*年"     # a year like 2023
)


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def bigram_overlap(text: str, source: str) -> float:
    """What fraction of text's character bigrams appear in source. Empty fields count 1.0 (empty is a signal)."""
    grams = _bigrams(text)
    if not grams:
        return 1.0
    source_grams = _bigrams(source)
    return len(grams & source_grams) / len(grams)


def _field_text(field: Field) -> str:
    value = field.value
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    if isinstance(value, dict):
        return "".join(str(v) for v in value.values())
    return str(value)


def field_scores(fields: dict[str, Field], answers: list[RawAnswer]) -> dict[str, float]:
    source = "".join(a.text for a in answers)
    return {
        name: bigram_overlap(_field_text(fields[name]), source)
        for name in DIFFERENTIATING_FIELDS
        if name in fields
    }


def flag_low_fidelity(scores: dict[str, float], threshold: float) -> list[str]:
    return sorted(name for name, score in scores.items() if score < threshold)


def suggest_threshold(scores: list[float], margin: float = 0.05) -> float:
    """Calibrate: take the lowest overlap judged a faithful extraction, then step down one notch."""
    if not scores:
        return 0.0
    return max(0.0, min(scores) - margin)


def unplaced_answers(
    fields: dict[str, Field], answers: list[RawAnswer], threshold: float = 0.5
) -> list[int]:
    """Which answers never made it into any field.

    The extractor outputs only the three differentiating fields; the adaptive third question may
    elicit content with no landing spot, and that whole answer is then dropped. Dropping is allowed -
    **vanishing silently is not** - so they are reported here, shown to the author in $EDITOR.
    """
    placed = "".join(_field_text(fields[n]) for n in DIFFERENTIATING_FIELDS if n in fields)
    out: list[int] = []
    for answer in sorted(answers, key=lambda a: a.n):
        if not answer.text.strip():
            continue
        if bigram_overlap(answer.text, placed) < threshold:
            out.append(answer.n)
    return out


def citation_flags(fields: dict[str, Field]) -> dict[str, list[str]]:
    """Flag suspected clause numbers, percentages, and years for human verification.

    Deliberately not a build assertion: some citations are correct, and a hard gate would force people
    to delete correct content. But never silent either - under Route B this text is sold to compliance teams; a wrong clause number is a real risk.
    """
    out: dict[str, list[str]] = {}
    for name, field in sorted(fields.items()):
        hits = sorted(set(_CITATION_RE.findall(_field_text(field))))
        if hits:
            out[name] = hits
    return out
