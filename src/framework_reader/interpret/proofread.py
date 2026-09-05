"""The proofread pass: language only, never content.

Roughly a third of the drafted 106 controls carried visible language defects (broken sentences, wrong register, mixed quote marks).
The content layer is fine; the language layer is not - this pass treats language only.

**Risk and countermeasure**: ask a model to rewrite prose and it will quietly change meaning - guaranteed.
So every single edit must pass classify_edit: only verdicts of pure language correction auto-persist;
"""
import re

from pydantic import BaseModel

from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.lint import bigram_overlap
from framework_reader.interpret.model import ALL_FIELDS, Basis, Field
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

# Factual markers: numbers, years, percentages, and latin strings like GDPR / NIS2 / SIEM / DBA.
    # Before and after proofreading these must match exactly - none added, none lost.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.-]{1,}|\d+")

# A language fix should not mangle the sentence. Below this overlap it is a rewrite, not a proofread.
_MIN_OVERLAP = 0.6

# Register drift: colloquial -> formal. The classifier cannot catch it, because the content did not
# change - but auditor_asks is valuable exactly because it sounds spoken; formalising it erases the selling point.
_DRIFT_PAIRS = (
    ("在哪", "在何处"),
    ("问", "询问"),
    ("没人", "无人"),
    ("怎么", "如何"),
    ("多久", "多长时间"),
    ("有没有", "是否存在"),
    ("说", "阐述"),
    ("看", "查阅"),
)


def _register_drift(before: str, after: str) -> list[str]:
    """List spots where colloquial wording was formalised. Counts only when the original had it and the edit does not."""
    hits: list[str] = []
    for spoken, formal in _DRIFT_PAIRS:
        if formal not in after or formal in before:
            continue
        # The formal word often contains the colloquial one entirely; carve it out before counting, or the
        if after.replace(formal, "").count(spoken) < before.replace(formal, "").count(spoken):
            hits.append(f"{spoken}→{formal}")
    return hits


class EditVerdict(BaseModel):
    ok: bool
    reason: str = ""


class EditFlag(BaseModel):
    field: str
    before: str
    after: str
    reason: str


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}. {v}" for k, v in sorted(value.items()))
    return str(value)


def classify_edit(before: str, after: str) -> EditVerdict:
    """Decide whether one edit is pure language correction or touched the content."""
    if before == after:
        return EditVerdict(ok=True)
    if before.strip() and not after.strip():
        return EditVerdict(ok=False, reason="content deleted wholesale")

    before_tokens = sorted(_TOKEN_RE.findall(before))
    after_tokens = sorted(_TOKEN_RE.findall(after))
    if before_tokens != after_tokens:
        lost = sorted(set(before_tokens) - set(after_tokens))
        gained = sorted(set(after_tokens) - set(before_tokens))
        bits = []
        if lost:
            bits.append(f"Lost {lost}")
        if gained:
            bits.append(f"Gained {gained}")
        return EditVerdict(ok=False, reason="; ".join(bits) or "fact markers changed")

    overlap = bigram_overlap(after, before)
    if overlap < _MIN_OVERLAP:
        return EditVerdict(ok=False, reason=f"Overlap {overlap:.2f} too low; reads like a rewrite, not a proofread")

    drift = _register_drift(before, after)
    if drift:
        return EditVerdict(ok=False, reason=f"Register drift (colloquial made formal): {'; '.join(drift)}")
    return EditVerdict(ok=True)


def proofread_fields(
    client: LLMClient,
    *,
    control_id: str,
    fields: dict[str, Field],
    model: str,
) -> tuple[dict[str, Field], list[EditFlag]]:
    """Returns (proofread fields, blocked suspicious edits). Suspicious edits are never persisted."""
    payload = {name: fields[name].value for name in ALL_FIELDS if name in fields}
    user = f"Control: {control_id}\n\n" + _as_json(payload)
    data = parse_json_object(
        client.complete(
            load_prompt("proofreader"), [Message(role="user", content=user)], model=model
        )
    )

    out: dict[str, Field] = dict(fields)
    flags: list[EditFlag] = []
    for name in ALL_FIELDS:
        if name not in fields or name not in data:
            continue
        original = fields[name]
        # Practitioner-written: the model may not touch a single word.
        if original.basis is Basis.PRACTITIONER:
            continue
        before, after = _text(original.value), _text(data[name])
        verdict = classify_edit(before, after)
        if not verdict.ok:
            flags.append(EditFlag(
                field=name, before=before, after=after, reason=verdict.reason
            ))
            continue
        out[name] = Field(value=data[name], basis=original.basis)
    return out, flags


def _as_json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
