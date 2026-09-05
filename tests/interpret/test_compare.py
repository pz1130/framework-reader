import json

from framework_reader.interpret.compare import (
    LintConfig,
    cross_provider_extract,
    diff_against_golden,
    render_diff_table,
)
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import FakeClient


def _interp(myth: str | None, asks: list[str] | None) -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value=myth, basis=Basis.PRACTITIONER)
    fields["auditor_asks"] = Field(value=asks, basis=Basis.PRACTITIONER)
    return Interpretation(control_id="NIST-CSF-2.0:PR.AA-05", fields=fields)


def test_diff_covers_the_three_differentiating_fields_only():
    diffs = diff_against_golden(
        _interp("手写的误解", ["手写追问"]), _interp("产出的误解", ["产出追问"])
    )
    assert [d.field for d in diffs] == list(DIFFERENTIATING_FIELDS)


def test_diff_marks_a_field_the_pipeline_left_empty():
    diffs = diff_against_golden(_interp("手写的误解", ["a"]), _interp(None, ["a"]))
    myth = next(d for d in diffs if d.field == "common_myth")
    assert myth.produced_empty is True
    assert myth.golden_empty is False


def test_diff_reports_length_ratio_as_a_bluntness_signal():
    """产出比手写短一大截，通常意味着塌成了通用表述。W2 spec §8.3"""
    diffs = diff_against_golden(
        _interp("手写的误解很长很具体还举了例子", ["a"]), _interp("很空泛", ["a"])
    )
    myth = next(d for d in diffs if d.field == "common_myth")
    assert myth.length_ratio < 0.5


def test_render_diff_table_names_both_sides():
    table = render_diff_table(diff_against_golden(_interp("手写", ["a"]), _interp("产出", ["a"])))
    assert "Golden" in table and "Produced" in table and "common_myth" in table


def test_cross_provider_runs_the_same_answers_through_each_client():
    payload = json.dumps(
        {"common_myth": "以为有张权限表就行", "auditor_asks": None, "regional_note": None},
        ensure_ascii=False,
    )
    questions = [Question(n=1, kind="fixed", text="q1"),
                 Question(n=2, kind="fixed", text="q2"),
                 Question(n=3, kind="adaptive", text="q3")]
    answers = [RawAnswer(n=n, text=f"以为有张权限表就行 {n}") for n in (1, 2, 3)]
    result = cross_provider_extract(
        {"deepseek": FakeClient([payload]), "glm": FakeClient([payload])},
        control_id="NIST-CSF-2.0:PR.AA-05",
        questions=questions,
        answers=answers,
        models={"deepseek": "deepseek-chat", "glm": "glm-4-plus"},
    )
    assert set(result) == {"deepseek", "glm"}
    assert result["glm"]["common_myth"].value == "以为有张权限表就行"


def test_lint_config_round_trips(tmp_path):
    path = tmp_path / "lint.yaml"
    path.write_text("bigram_threshold: 0.42\n", encoding="utf-8")
    assert LintConfig.load(path).bigram_threshold == 0.42


def test_lint_config_has_a_shipped_default():
    assert 0.0 <= LintConfig.load().bigram_threshold <= 1.0
