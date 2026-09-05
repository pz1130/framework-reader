"""用户改自己框架的解读。主 spec §5、§7.3.5

模型起草的是初稿。产品的价值不在初稿，在于**用户的经验落进去之后这段话
有人认领**——一份没人认领的合规文档，准不准都没人敢交出去。

basis 记的是谁写的：AI 写的 inferred，人写的 practitioner。签字是另一件事，
落在 state 上，且改过之后签名作废（W2 spec §4.3：签完没被改过才算数）。
"""
import pytest

from framework_reader.interpret.authoring import confirm, write_field
from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationState,
)
from framework_reader.interpret.user_store import UserInterpretationStore

CID = "ACME-SEC-2026:4.1"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))


@pytest.fixture
def store():
    return UserInterpretationStore()


def _drafted(store):
    store.save(Interpretation(
        control_id=CID,
        fields={
            name: Field(
                value=({"1": "一档", "2": "二档", "3": "三档"} if name == "practice"
                       else ["问题一"] if name == "auditor_asks" else "模型写的"),
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
    ))


def test_an_edited_field_is_marked_as_written_by_a_person(store):
    _drafted(store)
    write_field(store, CID, "intent", "我自己的说法")
    field = store.load(CID).fields["intent"]
    assert field.value == "我自己的说法" and field.basis is Basis.PRACTITIONER


def test_the_other_fields_are_left_alone(store):
    """改一个字段不该把整条重写一遍。"""
    _drafted(store)
    write_field(store, CID, "intent", "我自己的说法")
    assert store.load(CID).fields["plain_zh"].basis is Basis.INFERRED


def test_the_three_rungs_survive_as_three(store):
    _drafted(store)
    write_field(store, CID, "practice", {"1": "先做这个", "2": "再做这个", "3": "最后"})
    assert store.load(CID).fields["practice"].value["2"] == "再做这个"


def test_writing_on_a_control_with_no_draft_creates_one(store):
    """不想让模型碰的条款，用户应当能直接自己写。"""
    write_field(store, CID, "intent", "我自己写的，没让模型碰")
    loaded = store.load(CID)
    assert loaded.fields["intent"].value == "我自己写的，没让模型碰"
    assert loaded.fields["plain_zh"].value is None


def test_a_hand_written_control_claims_no_ai_authorship(store):
    write_field(store, CID, "intent", "我写的")
    assert all(f.basis is Basis.PRACTITIONER for f in store.load(CID).fields.values())


def test_clearing_a_field_empties_it_without_losing_the_rest(store):
    _drafted(store)
    write_field(store, CID, "regional_note", "")
    loaded = store.load(CID)
    assert loaded.fields["regional_note"].value in (None, "")
    assert loaded.fields["intent"].value == "模型写的"


# ---------- 签字 ----------

def test_confirming_records_who_and_when(store):
    _drafted(store)
    confirm(store, CID, signer="jc")
    loaded = store.load(CID)
    assert loaded.state is InterpretationState.CONFIRMED
    assert loaded.provenance.confirmed_by == "jc"
    assert loaded.provenance.confirmed_at is not None


def test_confirming_records_what_was_signed(store):
    """签的是当时那份内容。没有摘要就无从判断签完有没有被改。"""
    _drafted(store)
    confirm(store, CID, signer="jc")
    assert store.load(CID).provenance.signed_digest


def test_ai_cannot_sign(store):
    _drafted(store)
    with pytest.raises(ValueError, match="cannot sign"):
        confirm(store, CID, signer="ai:deepseek")


def test_confirming_something_that_does_not_exist_says_so(store):
    with pytest.raises(FileNotFoundError):
        confirm(store, CID, signer="jc")


def test_editing_after_signing_voids_the_signature(store):
    """签完又改，签名就不再覆盖这份内容。W2 spec §4.3"""
    _drafted(store)
    confirm(store, CID, signer="jc")
    write_field(store, CID, "intent", "又改了一版")
    loaded = store.load(CID)
    assert loaded.state is InterpretationState.DRAFT
    assert loaded.provenance.confirmed_by is None
    assert loaded.provenance.signed_digest is None


def test_the_drafting_model_is_still_recorded_after_you_edit(store):
    """你改了哪几句是一回事，这条最初是谁起草的是另一回事，别抹掉。"""
    from framework_reader.interpret.model import InterpretationProvenance, ModelRef

    store.save(Interpretation(
        control_id=CID,
        fields={n: Field(value="x", basis=Basis.INFERRED) for n in ALL_FIELDS},
        provenance=InterpretationProvenance(drafter=ModelRef(
            provider="deepseek", model="deepseek-chat", prompt_version="v3")),
    ))
    write_field(store, CID, "intent", "我改的")
    assert store.load(CID).provenance.drafter.model == "deepseek-chat"


def test_an_unknown_field_is_refused(store):
    _drafted(store)
    with pytest.raises(ValueError, match="No such field"):
        write_field(store, CID, "不存在的字段", "x")
