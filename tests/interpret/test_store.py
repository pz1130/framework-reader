import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
)
from framework_reader.interpret.store import InterpretationStore


def _interp(control_id: str = "NIST-CSF-2.0:GV.SC-07") -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    return Interpretation(control_id=control_id, fields=fields)


def test_path_is_one_file_per_control_under_framework_dir(tmp_path):
    store = InterpretationStore(tmp_path)
    path = store.path_for("NIST-CSF-2.0:GV.SC-07")
    assert path == tmp_path / "NIST-CSF-2.0" / "GV.SC-07.yaml"


def test_round_trip_preserves_everything(tmp_path):
    store = InterpretationStore(tmp_path)
    original = _interp()
    store.save(original)
    assert store.load("NIST-CSF-2.0:GV.SC-07") == original


def test_yaml_is_human_readable_utf8(tmp_path):
    """内容进 Git，作者要能直接读 diff——不许 ASCII 转义。"""
    store = InterpretationStore(tmp_path)
    interp = _interp()
    interp.fields["intent"] = Field(value="供应链风险不是签一次合同就完", basis=Basis.INFERRED)
    text = store.save(interp).read_text(encoding="utf-8")
    assert "供应链风险不是签一次合同就完" in text
    assert "\\u" not in text


def test_append_raw_persists_immediately(tmp_path):
    """作者说过的话，答完一问就落盘，不等三问答完。W2 spec §6"""
    store = InterpretationStore(tmp_path)
    store.save(_interp())
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="他们以为有张权限表就行")
    reloaded = store.load("NIST-CSF-2.0:GV.SC-07")
    assert [(r.n, r.text) for r in reloaded.interview.raw] == [
        (1, "他们以为有张权限表就行")
    ]


def test_append_raw_is_idempotent_per_question(tmp_path):
    """重答同一问覆盖该问，不追加第二条——续跑不能产生重复。"""
    store = InterpretationStore(tmp_path)
    store.save(_interp())
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="第一版")
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="改口后的版本")
    raw = store.load("NIST-CSF-2.0:GV.SC-07").interview.raw
    assert [(r.n, r.text) for r in raw] == [(1, "改口后的版本")]


def test_by_state_filters(tmp_path):
    store = InterpretationStore(tmp_path)
    store.save(_interp("NIST-CSF-2.0:GV.SC-07"))
    other = _interp("NIST-CSF-2.0:PR.AA-05")
    other.state = InterpretationState.INTERVIEWED
    store.save(other)
    drafts = store.by_state(InterpretationState.DRAFT)
    assert [i.control_id for i in drafts] == ["NIST-CSF-2.0:GV.SC-07"]


def test_iter_all_is_sorted_and_stable(tmp_path):
    store = InterpretationStore(tmp_path)
    for cid in ("NIST-CSF-2.0:PR.AA-05", "NIST-CSF-2.0:GV.SC-07"):
        store.save(_interp(cid))
    assert [i.control_id for i in store.iter_all()] == [
        "NIST-CSF-2.0:GV.SC-07", "NIST-CSF-2.0:PR.AA-05",
    ]


def test_load_missing_control_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        InterpretationStore(tmp_path).load("NIST-CSF-2.0:NOPE")


def test_save_is_atomic(tmp_path):
    """写盘中途崩不能留下半个文件——先写临时文件再 replace。"""
    store = InterpretationStore(tmp_path)
    path = store.save(_interp())
    assert path.exists()
    assert not list(path.parent.glob("*.tmp"))
