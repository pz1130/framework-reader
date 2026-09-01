"""Shipped interpretations must not greet a deployer with a review queue."""
from framework_reader.interpret.authoring import PUBLISHER_SIGNER
from framework_reader.interpret.model import InterpretationState
from framework_reader.interpret.store import InterpretationStore


def test_the_content_pack_has_no_unsigned_drafts():
    drafts = [
        i.control_id
        for i in InterpretationStore().iter_all()
        if i.state is not InterpretationState.CONFIRMED
    ]
    assert drafts == [], f"unsigned interpretations in the pack: {drafts[:8]}"


def test_shipped_confirmations_are_signed_by_the_publisher():
    items = list(InterpretationStore().iter_all())
    assert items
    assert all(i.provenance.confirmed_by == PUBLISHER_SIGNER for i in items)
    assert all(i.provenance.signed_digest for i in items)
