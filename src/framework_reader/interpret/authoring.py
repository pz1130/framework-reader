"""The user edits their framework's interpretations, and signs them. Main spec §5, §7.3.5

The drafter writes first drafts. The product's value is not the draft - it is that, once the user's
experience is in, every sentence has someone standing behind it. A compliance document nobody

Two things are recorded separately:
  - `basis` records **who wrote the sentence**: AI-written is inferred, human-written is practitioner. Per field.
  - `state` records **who stands behind the control**: signing lands on the whole control, and any later
    W2 spec §4.3 says it verbatim: "a signature counts only while unchanged".
"""
from datetime import datetime, timezone

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationState,
    fields_digest,
)

FieldValue = str | list[str] | dict[str, str] | None


def blank(control_id: str) -> Interpretation:
    """An interpretation nobody has written yet. All seven fields empty, and all counted as human-written -

    something the model never touched must not carry an AI name.
    """
    return Interpretation(
        control_id=control_id,
        fields={n: Field(value=None, basis=Basis.PRACTITIONER) for n in ALL_FIELDS},
    )


def write_field(
    store, control_id: str, field: str, value: FieldValue,
    basis: Basis = Basis.PRACTITIONER,
) -> Interpretation:
    """Write one field. Every other field is untouched.

    `basis` records **who wrote this sentence**: typed by the user, it is practitioner; requested by the
    user and written by the model, it is inferred - the request was theirs, the words are the model's,
    and marking it practitioner claims words the user never wrote.
    """
    if field not in ALL_FIELDS:
        raise ValueError(f"No such field: {field}")
    interp = store.load(control_id) if store.exists(control_id) else blank(control_id)
    fields = dict(interp.fields)
    fields[field] = Field(value=value, basis=basis)

    provenance = interp.provenance.model_copy()
    state = interp.state
    if state is InterpretationState.CONFIRMED:
        # Signed then edited: the signature no longer covers this content. Keeping it is more dangerous than none.
        # The drafter stays as-is - that is the control's provenance, not its signature.
        state = InterpretationState.DRAFT
        provenance.confirmed_by = None
        provenance.confirmed_at = None
        provenance.signed_digest = None

    updated = Interpretation(
        control_id=control_id, locale=interp.locale, state=state,
        fields=fields, interview=interp.interview, provenance=provenance,
    )
    store.save(updated)
    return updated


# Product-level signer for interpretations we ship. Not a model id (those
# are rejected as `ai:` / `model:`), and not a person's name — deployers
# should not see a 199-item review queue of unsigned AI drafts.
PUBLISHER_SIGNER = "publisher"


def confirm(store, control_id: str, *, signer: str) -> Interpretation:
    """Claim this control. Signs the content as it stands, digest recorded alongside."""
    interp = store.load(control_id)          # raises FileNotFoundError when missing
    provenance = interp.provenance.model_copy()
    provenance.confirmed_by = signer
    provenance.confirmed_at = datetime.now(timezone.utc)
    signed = Interpretation(
        control_id=interp.control_id, locale=interp.locale,
        state=InterpretationState.CONFIRMED, fields=interp.fields,
        interview=interp.interview, provenance=provenance,
    )
    # The digest excludes provenance, so signing first and backfilling later is safe. See model.fields_digest
    signed.provenance.signed_digest = fields_digest(signed)
    store.save(signed)
    return signed
