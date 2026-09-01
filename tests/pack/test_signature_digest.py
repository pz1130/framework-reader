"""签字覆盖的是内容，不是文件时间戳。W2 spec §4.3

mtime 不是内容有没有变的证据：git clone / CI checkout / 切分支都会刷新它。
"""
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from framework_reader.cli.interview import sign
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    fields_digest,
)
from framework_reader.interpret.store import InterpretationStore
from framework_reader.pack.validate import (
    BuildAssertionError,
    assert_signature_matches_content,
)

CID = "NIST-CSF-2.0:PR.AA-05"


def _draft() -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value="以为有张权限表就行", basis=Basis.PRACTITIONER)
    return Interpretation(control_id=CID, fields=fields)


def _signed(when: datetime | None = None) -> Interpretation:
    return sign(_draft(), "jc", when or datetime.now(timezone.utc))


def test_digest_is_stable_across_equal_content():
    assert fields_digest(_draft()) == fields_digest(_draft())


def test_digest_changes_when_a_field_changes():
    other = _draft()
    other.fields["common_myth"] = Field(value="改了", basis=Basis.PRACTITIONER)
    assert fields_digest(_draft()) != fields_digest(other)


def test_digest_changes_when_the_verbatim_answers_change():
    """raw 是签字时一并认下的证据，改了也算改。"""
    from framework_reader.interpret.model import RawAnswer

    other = _draft()
    other.interview.raw = [RawAnswer(n=1, text="事后补的原话")]
    assert fields_digest(_draft()) != fields_digest(other)


def test_digest_ignores_provenance_so_timing_can_be_recorded_later():
    signed = _signed()
    before = fields_digest(signed)
    signed.provenance.interview_seconds = 812.0
    assert fields_digest(signed) == before


def test_sign_stamps_the_digest():
    signed = _signed()
    assert signed.provenance.signed_digest == fields_digest(signed)


def test_untouched_content_passes(tmp_path):
    store = InterpretationStore(tmp_path)
    signed = _signed()
    store.save(signed)
    assert_signature_matches_content([store.load(CID)])


def test_edit_after_signing_fails_the_build(tmp_path):
    store = InterpretationStore(tmp_path)
    signed = _signed()
    store.save(signed)
    tampered = store.load(CID)
    tampered.fields["common_myth"] = Field(
        value="签完字之后偷偷改的", basis=Basis.PRACTITIONER
    )
    store.save(tampered)
    with pytest.raises(BuildAssertionError, match="please sign again"):
        assert_signature_matches_content([store.load(CID)])


def test_missing_digest_fails_the_build(tmp_path):
    store = InterpretationStore(tmp_path)
    interp = _draft()
    interp.state = InterpretationState.CONFIRMED
    interp.provenance = InterpretationProvenance(
        confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
    )
    store.save(interp)
    with pytest.raises(BuildAssertionError, match="signed_digest"):
        assert_signature_matches_content([store.load(CID)])


def test_survives_a_git_clone(tmp_path):
    """真实场景：上周签的字，今天在另一台机器 clone 出来构建。

    git 恢复文件时打的是当前时间——用 mtime 判断内容有没有变，这里必然误报。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)

    store = InterpretationStore(repo / "content/interpretations")
    store.save(_signed(datetime.now(timezone.utc) - timedelta(days=7)))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "signed last week"], cwd=repo, check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    cloned = InterpretationStore(clone / "content/interpretations").load(CID)
    assert_signature_matches_content([cloned])
