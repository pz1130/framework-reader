from datetime import datetime, timezone

import pytest
import yaml
from pydantic import ValidationError

from framework_reader.cli.interview import (
    already_done_message,
    annotated_yaml,
    render_header,
    run_editor,
    sign,
)
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
    RawAnswer,
)


def _interp() -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value="以为有张权限表就行", basis=Basis.PRACTITIONER)
    interp = Interpretation(control_id="NIST-CSF-2.0:PR.AA-05", fields=fields)
    interp.interview.raw = [RawAnswer(n=1, text="他们以为有张权限表就行，其实差远了")]
    return interp


def test_header_shows_position_and_control():
    header = render_header(_interp(), "Access permissions are defined", 3, 106)
    assert "PR.AA-05" in header
    assert "3/106" in header
    assert "Access permissions are defined" in header
    assert "^D" not in header
    assert "^S" not in header
    assert "^K" not in header
    assert "展开全部" not in header


def test_already_done_message_refuses_interviewed_without_force():
    interp = _interp()
    interp.state = InterpretationState.INTERVIEWED
    msg = already_done_message(interp, force=False)
    assert msg is not None
    assert "INTERVIEWED" in msg or "interviewed" in msg
    assert "--force" in msg
    assert already_done_message(interp, force=True) is None


def test_already_done_message_allows_draft():
    assert already_done_message(_interp(), force=False) is None


def test_annotated_yaml_puts_the_authors_own_words_above_the_field():
    text = annotated_yaml(_interp(), scores={}, threshold=0.5)
    lines = text.splitlines()
    idx = next(i for i, l in enumerate(lines) if l.strip().startswith("common_myth:"))
    assert any("他们以为有张权限表就行，其实差远了" in l for l in lines[max(0, idx - 4):idx])


def test_annotated_yaml_flags_fields_below_threshold():
    text = annotated_yaml(_interp(), scores={"common_myth": 0.11}, threshold=0.5)
    assert "Low extraction fidelity" in text
    assert "0.11" in text


def test_annotated_yaml_is_still_parseable_yaml():
    """注释不能把文件写坏——签完字它就是要 ship 的那个文件。"""
    text = annotated_yaml(_interp(), scores={"common_myth": 0.11}, threshold=0.5)
    reloaded = Interpretation(**yaml.safe_load(text))
    assert reloaded.control_id == "NIST-CSF-2.0:PR.AA-05"
    assert reloaded.fields["common_myth"].value == "以为有张权限表就行"


def test_sign_moves_to_confirmed_with_who_and_when():
    now = datetime(2026, 8, 25, 14, 3, tzinfo=timezone.utc)
    signed = sign(_interp(), "jc", now)
    assert signed.state is InterpretationState.CONFIRMED
    assert signed.provenance.confirmed_by == "jc"
    assert signed.provenance.confirmed_at == now


def test_ai_cannot_sign():
    with pytest.raises(ValidationError):
        sign(_interp(), "ai:deepseek-chat", datetime.now(timezone.utc))


def test_run_editor_invokes_the_configured_command(tmp_path):
    calls: list = []
    path = tmp_path / "x.yaml"
    path.write_text("k: v", encoding="utf-8")
    run_editor(path, "vim", runner=lambda argv, check: calls.append((argv, check)))
    assert calls == [(["vim", str(path)], True)]


def test_annotated_yaml_warns_about_answers_that_landed_nowhere():
    """编辑器里必须看得见「这句话没进任何字段」，否则作者不会发现。"""
    interp = _interp()
    interp.interview.raw = [
        RawAnswer(n=1, text="他们以为有张权限表就行"),
        RawAnswer(n=3, text="接入siem，写usecase"),
    ]
    interp.interview.unplaced = [3]
    text = annotated_yaml(interp, scores={}, threshold=0.5)
    assert "landed in no field" in text
    assert "接入siem，写usecase" in text
    import yaml as _yaml
    assert Interpretation(**_yaml.safe_load(text)).interview.unplaced == [3]
