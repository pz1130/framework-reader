"""The drafter: the four non-differentiating fields. W2 spec §2, first row of the table

The three differentiating fields are dropped even when the model offers them - the first gate of D1.
"""
import json
from pathlib import Path

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import PROMPT_VERSIONS, load_prompt

__all__ = [
    "DrafterOutputError", "draft_fields", "draft_full_fields", "PROMPT_VERSIONS",
]


DRAFT_FAILURE_DIR = Path("build/draft_failures")


class DrafterOutputError(Exception):
    """The model output violates the agreed structure. No guessing, no repair: fail directly."""


_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _repair_json(text: str) -> str:
    """Repairs exactly two syntax defects: bare control characters inside strings, and trailing commas.

    Scans character by character while tracking string state, so commas and brackets inside strings
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in _ESCAPES:
                out.append(_ESCAPES[ch])
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch in "}]":
            # Fall back over the immediately preceding trailing comma (and any whitespace)
            trailing: list[str] = []
            while out and out[-1].isspace():
                trailing.append(out.pop())
            if out and out[-1] == ",":
                out.pop()
            out.extend(reversed(trailing))
        out.append(ch)
    return "".join(out)


def parse_json_object(text: str) -> dict:
    """Tolerates ```json fences; everything else counts as malformed."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # Pure syntax repair: escape bare newlines inside strings, drop trailing commas. Not one word of
        # content changes. This does not contradict "the extractor never auto-repairs" - that bans
        try:
            data = json.loads(_repair_json(stripped))
        except json.JSONDecodeError as exc:
            raise DrafterOutputError(f"not valid JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise DrafterOutputError(f"top level is not an object: {text[:200]}")
    return data


def draft_fields(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    neighbors: list[str],
    model: str,
) -> dict[str, Field]:
    user = (
        f"Control: {control_id}\n"
        f"Framework text (public domain): {outcome}\n"
        f"Officially mapped 800-53 controls: {', '.join(neighbors) if neighbors else '(none)'}"
    )
    raw = client.complete(
        load_prompt("drafter"), [Message(role="user", content=user)], model=model
    )
    data = parse_json_object(raw)

    for name in DRAFTED_FIELDS:
        if name not in data or data[name] in (None, "", {}, []):
            raise DrafterOutputError(f"missing or empty field: {name}")

    # Per-field type validation. Field.value is str | list | dict | None and Pydantic accepts any shape,
    # so the schema cannot catch it - one vendor really did return evidence as three rungs.
    for name in ("intent", "plain_zh", "evidence"):
        if not isinstance(data[name], str):
            raise DrafterOutputError(
                f"{name} must be a string, got {type(data[name]).__name__}: {data[name]!r}"
            )

    practice = data["practice"]
    if not isinstance(practice, dict) or set(practice) != {"1", "2", "3"}:
        raise DrafterOutputError(f"practice must be a three-level dict, got: {practice!r}")
    bad_levels = {k: v for k, v in practice.items() if not isinstance(v, str)}
    if bad_levels:
        raise DrafterOutputError(f"every level of practice must be a string, got: {bad_levels!r}")

    # Differentiating fields are always dropped, even when the model volunteers them.
    return {
        name: Field(value=data[name], basis=Basis.INFERRED) for name in DRAFTED_FIELDS
    }


def _check_str(data: dict, name: str, *, allow_null: bool) -> None:
    value = data.get(name)
    if value is None and allow_null:
        return
    if not isinstance(value, str) or not value.strip():
        raise DrafterOutputError(
            f"{name} must be a non-empty string{' or null' if allow_null else ''}, "
            f"got {type(value).__name__}: {value!r}"
        )


def _render_examples(examples: list) -> str:
    """Render handwritten samples as few-shot. What is transmitted is granularity, not content."""
    if not examples:
        return ""
    blocks: list[str] = []
    for ex in examples:
        payload = {
            name: ex.fields[name].value for name in ALL_FIELDS if name in ex.fields
        }
        blocks.append(
            f"### Example: {ex.control_id}\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n```"
        )
    return (
        "\n\n---\n\n## Granularity examples (hand-written by auditors; shown in another\n"
    "## language - your output stays in English)\n\n"
        "Below is what the same set of fields looks like at **acceptable granularity**. **Do not copy their content** - "
        "they describe other controls. What to learn is the level of specificity:\n\n"
        "- `auditor_asks`: sentences an auditor would actually say out loud, the kind where a non-answer gives you away, "
        '- not yes/no questions such as "do you have a periodic process";\n'
        '- `common_myth`: names **exactly which step is wrong**, not correct-sounding platitudes such as "not enough emphasis";\n'
        "- `regional_note`: null when there is no real regional difference.\n\n"
        + "\n\n".join(blocks)
    )


def draft_full_fields(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    neighbors: list[str],
    model: str,
    label: str = "",
    grounding: list[str] | None = None,
    practice: list[str] | None = None,
    examples: list | None = None,
    failure_dir: Path | None = DRAFT_FAILURE_DIR,
) -> dict[str, Field]:
    """Route B: all seven fields written by AI. Main spec §5 (revised 2026-08-20)

    Output is always marked basis=inferred - AI-written says so. Fields the author later edits in
    edited by the author are re-marked practitioner by the sign-off flow.

    An empty `outcome` = this framework's source text is copyrighted: not given, and not allowed
    The grounding material is then self-written titles + `grounding` (the officially mapped 800-53
    The payload must say outright that the source text was not given - otherwise the model treats the

    `practice` holds **excerpts from the organization's own policies** (uploaded companion documents, design §8 S5).
    It is kept separate from `grounding` in the payload because they are different claims: 800-53 says
    "what should be"; the house policy says "what is". Mixed together, the model turns
    "we already do this" into "the standard requires this".
    """
    if outcome:
        user = (
            f"Control: {control_id}\n"
            f"Framework text (public domain): {outcome}\n"
            f"Officially mapped 800-53 controls: {', '.join(neighbors) if neighbors else '(none)'}"
        )
    else:
        lines = [
            f"Control: {control_id}",
            "The original text of this framework's control is **copyrighted and not provided**. Do not pretend to have read it, and do not translate "
            "the short title on the next line - that is our own short title, not the standard's text.",
            f"Self-written title: {label}",
        ]
        if grounding:
            lines.append(
                "Original text of the officially mapped NIST SP 800-53 controls (public domain; infer from it what this control defends against):"
            )
            lines += [f"- {line}" for line in grounding]
        else:
            lines.append("There is no official mapping to lean on; infer from the title and the framework structure.")
        user = "\n".join(lines)
    if practice:
        user += "\n" + _our_practice(practice)
    system = load_prompt("drafter_full") + _render_examples(examples or [])
    raw = client.complete(system, [Message(role="user", content=user)], model=model)

    def fail(message: str) -> DrafterOutputError:
        if failure_dir is not None:
            failure_dir.mkdir(parents=True, exist_ok=True)
            name = control_id.replace(":", "_").replace("/", "_")
            (failure_dir / f"{name}.txt").write_text(raw, encoding="utf-8")
        return DrafterOutputError(message)

    try:
        data = parse_json_object(raw)
    except DrafterOutputError as exc:
        raise fail(str(exc)) from exc

    missing = [n for n in ALL_FIELDS if n not in data]
    if missing:
        raise fail(f"missing fields: {missing}")

    for name in ("intent", "plain_zh"):
        _check_str(data, name, allow_null=False)
    _check_str(data, "evidence", allow_null=False)
    for name in ("common_myth", "regional_note"):
        _check_str(data, name, allow_null=True)

    practice = data["practice"]
    if not isinstance(practice, dict) or set(practice) != {"1", "2", "3"}:
        raise DrafterOutputError(f"practice must be a three-level dict, got: {practice!r}")
    bad = {k: v for k, v in practice.items() if not isinstance(v, str)}
    if bad:
        raise DrafterOutputError(f"every level of practice must be a string, got: {bad!r}")

    asks = data["auditor_asks"]
    if asks is not None:
        if not isinstance(asks, list) or not all(isinstance(a, str) for a in asks):
            raise DrafterOutputError(
                f"auditor_asks must be a list of strings or null, got: {asks!r}"
            )

    return {name: Field(value=data[name], basis=Basis.INFERRED) for name in ALL_FIELDS}


def rewrite_field(
    client: LLMClient,
    *,
    control_id: str,
    field: str,
    label: str,
    current,
    instruction: str,
    model: str,
    outcome: str = "",
) -> object:
    """Rewrite one field per the user's one-line instruction. 2026-08-23

    The third piece of "user and AI interpret together": the user sees what is wrong but may not want
    to write it themselves. They give direction ("be more specific, name the systems"); the model executes.

    The output is still AI-written, persisted as `inferred` - **the request was theirs, the words are the model's**.
    Marking it practitioner would claim words on the user's behalf that they never wrote.
    """
    if not instruction.strip():
        raise DrafterOutputError("the instruction must not be empty; with no instruction there is nothing to rewrite")

    lines = [f"Control: {control_id}", f"Field: {field} ({label})"]
    if outcome:
        lines.append(f"Body of this control (the user's own policy text): {outcome}")
    else:
        lines.append("No body text is available for this control; rewrite from the field's current content only. Do not invent new facts.")
    lines.append(
        "Current content:\n" + json.dumps(current, ensure_ascii=False, indent=2)
    )
    lines.append(f"The user's instruction: {instruction.strip()}")

    raw = client.complete(
        load_prompt("rewriter"),
        [Message(role="user", content="\n\n".join(lines))],
        model=model,
    )
    data = parse_json_object(raw)
    if "value" not in data:
        raise DrafterOutputError(f"output has no value key: {sorted(data)}")
    return _checked_value(field, data["value"])


def _our_practice(lines: list[str]) -> str:
    """Excerpts from the organization's own policies. **Must be labelled separately from standard text.**

    Otherwise the model turns "we already do this" into "the standard requires this" -
    which breaks the very thing this product stands for: knowing who said what.
    """
    return "\n".join([
        "Excerpts from this organization's own policies (below is what this company **already does**, not what the standard requires):",
        *(f"- {line}" for line in lines),
        "When writing practice and evidence, align with the existing practices above, "
        "and quote their specific numbers and department names where appropriate. "
        "**Never invent specific numbers, cadences, or department names not listed above.**",
    ])


def _checked_value(field: str, value):
    """Reject a wrong shape. When the shape collapses, practice degrades from three rungs to one sentence."""
    if field == "practice":
        if not isinstance(value, dict) or set(value) != {"1", "2", "3"}:
            raise DrafterOutputError(f"practice must be a three-level dict, got: {value!r}")
        bad = {k: v for k, v in value.items() if not isinstance(v, str)}
        if bad:
            raise DrafterOutputError(f"every level of practice must be a string, got: {bad!r}")
        return value
    if field == "auditor_asks":
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(a, str) for a in value)
        ):
            raise DrafterOutputError(f"auditor_asks must be a list of strings, got: {value!r}")
        return value
    if value is not None and not isinstance(value, str):
        raise DrafterOutputError(f"{field} must be a string, got: {value!r}")
    return value
