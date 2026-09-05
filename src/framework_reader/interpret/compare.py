"""Golden-sample diff, cross-vendor comparison, and lint config. W2 spec §3.5, §8"""
from pathlib import Path

import yaml
from pydantic import BaseModel

from framework_reader.interpret.extractor import extract_fields
from framework_reader.interpret.lint import bigram_overlap
from framework_reader.interpret.model import (
    DIFFERENTIATING_FIELDS,
    Field,
    Interpretation,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import LLMClient

DEFAULT_LINT_PATH = Path("content/lint.yaml")


class LintConfig(BaseModel):
    bigram_threshold: float

    @classmethod
    def load(cls, path: Path = DEFAULT_LINT_PATH) -> "LintConfig":
        return cls(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _text(field: Field) -> str:
    value = field.value
    if value is None:
        return ""
    if isinstance(value, list):
        return " / ".join(str(v) for v in value)
    return str(value)


class FieldDiff(BaseModel):
    field: str
    golden: str
    produced: str
    golden_empty: bool
    produced_empty: bool
    overlap: float
    length_ratio: float


def diff_against_golden(
    golden: Interpretation, produced: Interpretation
) -> list[FieldDiff]:
    diffs: list[FieldDiff] = []
    for name in DIFFERENTIATING_FIELDS:
        g = _text(golden.fields[name])
        p = _text(produced.fields[name])
        diffs.append(FieldDiff(
            field=name,
            golden=g,
            produced=p,
            golden_empty=not g,
            produced_empty=not p,
            overlap=bigram_overlap(p, g) if p else 0.0,
            length_ratio=(len(p) / len(g)) if g else (1.0 if not p else 0.0),
        ))
    return diffs


def render_diff_table(diffs: list[FieldDiff]) -> str:
    lines = ["| Field | Golden | Produced | Overlap | Length ratio |", "|---|---|---|---|---|"]
    for d in diffs:
        lines.append(
            f"| {d.field} | {d.golden[:40]} | {d.produced[:40]} | "
            f"{d.overlap:.2f} | {d.length_ratio:.2f} |"
        )
    return "\n".join(lines)


def cross_provider_extract(
    clients: dict[str, LLMClient],
    *,
    control_id: str,
    questions: list[Question],
    answers: list[RawAnswer],
    models: dict[str, str],
    failure_dir: Path | None = None,
) -> dict[str, dict[str, Field]]:
    """Run one batch of answers through multiple vendors so the author can pick one before W3. W2 spec §3.5"""
    return {
        provider: extract_fields(
            client, control_id=control_id, questions=questions,
            answers=answers, model=models[provider], failure_dir=failure_dir,
        )
        for provider, client in sorted(clients.items())
    }
