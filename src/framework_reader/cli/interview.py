"""Interview terminal shell and $EDITOR sign-off. W2 spec §5

The logic lives in interpret/session.py; this file only renders and reads the keyboard.
"""
import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

from framework_reader.interpret.lint import field_scores
from framework_reader.interpret.model import (
    DIFFERENTIATING_FIELDS,
    Interpretation,
    InterpretationState,
    Question,
    fields_digest,
)

Runner = Callable[[list[str], bool], object]
Ask = Callable[[Question], str]
Edit = Callable[[Path], None]


def render_header(
    interp: Interpretation, control_label: str, index: int, total: int
) -> str:
    local = interp.control_id.split(":", 1)[-1]
    intent = interp.fields["intent"].value or ""
    return (
        f"┌ {local} · {index}/{total} " + "─" * 40 + "\n"
        f"│ {control_label}\n"
        f"│\n"
        f"│ Draft intent  {intent}\n"
        + "└" + "─" * 62
    )


def already_done_message(interp: Interpretation, *, force: bool) -> str | None:
    """Refuse to silently re-interview an already interviewed/confirmed control; requires explicit --force."""
    if force:
        return None
    if interp.state in (InterpretationState.INTERVIEWED, InterpretationState.CONFIRMED):
        return (
            f"{interp.control_id} is already {interp.state.value}, "
            f"re-running the interview overwrites the extraction and the sign-off; pass --force to confirm"
        )
    return None


def annotated_yaml(
    interp: Interpretation, scores: dict[str, float], threshold: float
) -> str:
    """Paste the verbatim answers and lint results as comments above the differentiating fields. Comments do not affect YAML parsing."""
    payload = interp.model_dump(mode="json", exclude_none=False)
    text = yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    raw_all = " / ".join(r.text for r in interp.interview.raw)
    by_n = {r.n: r.text for r in interp.interview.raw}

    out: list[str] = []
    for n in interp.interview.unplaced:
        out.append(
            f"# ⚠ Answer {n} landed in no field; the verbatim answer was: {by_n.get(n, '')}"
        )
    if interp.interview.unplaced:
        out.append("#   - to keep it, paste it into one of the fields below; otherwise it only survives in interview.raw.")
        out.append("")

    for line in text.splitlines():
        stripped = line.strip()
        for name in DIFFERENTIATING_FIELDS:
            if stripped.startswith(f"{name}:"):
                indent = " " * (len(line) - len(line.lstrip()))
                out.append(f"{indent}# You said: {raw_all}")
                score = scores.get(name)
                if score is not None and score < threshold:
                    out.append(
                        f"{indent}# ⚠ Low extraction fidelity ({score:.2f} < {threshold:.2f})"
                        f" - does this passage read like the model wrote it?"
                    )
                break
        out.append(line)
    return "\n".join(out) + "\n"


def sign(interp: Interpretation, signer: str, now: datetime) -> Interpretation:
    """Sign. The digest covers the content as it was at signing; the build recomputes and compares it. W2 spec §4.3"""
    data = interp.model_dump()
    data["state"] = InterpretationState.CONFIRMED
    data["provenance"]["confirmed_by"] = signer
    data["provenance"]["confirmed_at"] = now
    signed = Interpretation(**data)
    signed.provenance.signed_digest = fields_digest(signed)
    return signed


def run_editor(path: Path, editor_cmd: str, runner: Runner = subprocess.run) -> None:
    runner([editor_cmd, str(path)], True)


def default_editor() -> str:
    return os.environ.get("EDITOR") or "vi"


def run_interview(
    store,
    session,
    control_id: str,
    *,
    ask: Ask,
    edit: Edit,
    signer: str,
    now: Callable[[], datetime],
    clock: Callable[[], float] = time.perf_counter,
    threshold: float,
) -> float:
    """Three questions → extraction → editor → sign-off. Returns this control's elapsed seconds.

    All IO is injected for end-to-end testing: W3 runs this 106 times, so this chain
    must not only ever be smoke-tested via --help.
    """
    started = clock()

    while (question := session.next_question(control_id)) is not None:
        session.record(control_id, question.n, ask(question))

    interp = session.finish(control_id, force=True)
    path = store.path_for(control_id)
    scores = field_scores(interp.fields, interp.interview.raw)
    path.write_text(annotated_yaml(interp, scores, threshold), encoding="utf-8")

    edit(path)

    edited = Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))
    elapsed = clock() - started
    signed = sign(edited, signer, now())
    # Elapsed time stays out of the digest (see fields_digest), so recording it
    # afterwards cannot invalidate the signature.
    signed.provenance.interview_seconds = elapsed
    store.save(signed)
    return elapsed
