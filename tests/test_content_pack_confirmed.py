"""The pack's two signing tiers on the English line.

NIST 800-53 ships publisher-signed, confirmed, English interpretations. The CSF and
ISO interpretations are machine-translated from the signed Chinese originals (which
live on the zh line): they ship as marked AI drafts — no fake sign-off — and a
deployer who trusts a translation confirms it, signing it under their own name.
"""
import json
import re

from framework_reader.interpret.authoring import PUBLISHER_SIGNER
from framework_reader.interpret.model import InterpretationState
from framework_reader.interpret.store import InterpretationStore

_CJK = re.compile(r"[\u4e00-\u9fff]")


def test_80053_is_confirmed_and_publisher_signed():
    items = [
        i for i in InterpretationStore().iter_all()
        if i.control_id.startswith("NIST-800-53-R5:")
    ]
    assert items
    assert all(i.state is InterpretationState.CONFIRMED for i in items)
    assert all(i.provenance.confirmed_by == PUBLISHER_SIGNER for i in items)
    assert all(i.provenance.signed_digest for i in items)


def test_csf_and_iso_ship_as_translated_drafts():
    items = [
        i for i in InterpretationStore().iter_all()
        if not i.control_id.startswith("NIST-800-53-R5:")
    ]
    assert items
    assert all(i.state is InterpretationState.DRAFT for i in items)
    assert all(i.locale == "en" for i in items)


def test_no_chinese_remains_in_any_shipped_interpretation():
    for i in InterpretationStore().iter_all():
        for name, field in i.fields.items():
            blob = json.dumps(field.value, ensure_ascii=False)
            assert not _CJK.search(blob), f"{i.control_id}.{name} still contains Chinese"
