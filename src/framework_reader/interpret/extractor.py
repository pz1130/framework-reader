"""抽取器：严格抽取三个差异化字段。W2 spec §2.3

只许删、切、重排；输出不合结构时不写盘、不自动修复。
"""
from pathlib import Path

from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Field,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

FAILURE_DIR = Path("build/extract_failures")


class ExtractorOutputError(Exception):
    """抽取器输出不合结构。不修、不写盘。"""


def _dump_failure(failure_dir: Path | None, control_id: str, raw: str) -> None:
    if failure_dir is None:
        return
    failure_dir.mkdir(parents=True, exist_ok=True)
    name = control_id.replace(":", "_").replace("/", "_")
    (failure_dir / f"{name}.txt").write_text(raw, encoding="utf-8")


def extract_fields(
    client: LLMClient,
    *,
    control_id: str,
    questions: list[Question],
    answers: list[RawAnswer],
    model: str,
    failure_dir: Path | None = FAILURE_DIR,
) -> dict[str, Field]:
    by_n = {q.n: q.text for q in questions}
    transcript = "\n\n".join(
        f"Q{a.n}: {by_n.get(a.n, '')}\nA{a.n}: {a.text}"
        for a in sorted(answers, key=lambda a: a.n)
    )
    user = f"Control: {control_id}\n\n{transcript}"

    raw = client.complete(
        load_prompt("extractor"), [Message(role="user", content=user)], model=model
    )
    try:
        data = parse_json_object(raw)
    except Exception as exc:
        _dump_failure(failure_dir, control_id, raw)
        raise ExtractorOutputError(f"extractor output is not valid JSON: {raw[:200]}") from exc

    for name in DIFFERENTIATING_FIELDS:
        if name not in data:
            _dump_failure(failure_dir, control_id, raw)
            raise ExtractorOutputError(f"missing key: {name}")

    if data["auditor_asks"] is not None and not isinstance(data["auditor_asks"], list):
        _dump_failure(failure_dir, control_id, raw)
        raise ExtractorOutputError(
            f"auditor_asks must be a list or null, got {type(data['auditor_asks']).__name__}"
        )
    for name in ("common_myth", "regional_note"):
        if data[name] is not None and not isinstance(data[name], str):
            _dump_failure(failure_dir, control_id, raw)
            raise ExtractorOutputError(
                f"{name} must be a string or null, got {type(data[name]).__name__}"
            )

    return {
        name: Field(value=data[name], basis=Basis.PRACTITIONER)
        for name in DIFFERENTIATING_FIELDS
    }
