from pathlib import Path

import pytest

from framework_reader.interpret.golden import GOLDEN_CONTROLS, GOLDEN_ROOT, load_golden
from framework_reader.interpret.model import Basis, DIFFERENTIATING_FIELDS, InterpretationState


def test_three_golden_controls_are_the_ones_the_spec_names():
    assert GOLDEN_CONTROLS == (
        "NIST-CSF-2.0:GV.SC-07",
        "NIST-CSF-2.0:PR.AA-05",
        "NIST-CSF-2.0:GV.RM-02",
    )


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_file_exists_and_parses(control_id):
    assert load_golden(control_id).control_id == control_id


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_is_confirmed_and_signed_by_a_human(control_id):
    golden = load_golden(control_id)
    assert golden.state is InterpretationState.CONFIRMED
    assert golden.provenance.confirmed_by
    assert not golden.provenance.confirmed_by.startswith("ai:")


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_has_no_model_provenance(control_id):
    """黄金样例零 AI 参与——一旦有 drafter/extractor 记录，它就不是尺子了。W2 spec §1.3"""
    golden = load_golden(control_id)
    assert golden.provenance.drafter is None
    assert golden.provenance.extractor is None


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_fills_at_least_two_differentiating_fields(control_id):
    """尺子本身必须有刻度：三个差异化字段至少两个有内容。"""
    golden = load_golden(control_id)
    filled = [n for n in DIFFERENTIATING_FIELDS if golden.fields[n].value]
    assert len(filled) >= 2, f"{control_id} 只填了 {filled}"
    for name in DIFFERENTIATING_FIELDS:
        assert golden.fields[name].basis is Basis.PRACTITIONER


def test_golden_root_is_separate_from_production():
    assert GOLDEN_ROOT == Path("content/golden")
    assert GOLDEN_ROOT != Path("content/interpretations")
