"""Version-change inheritance: copy an interpretation already written on the old
clause onto the new clause.

Three things that do not follow (version-change inheritance plan, Global
Constraints):
- **Signatures do not follow** - a signature is made against the old clause's
  source text and fields; a different clause means signing again. Inherited
  results are always `draft`, so the build gate is naturally unaffected;
- **Interview raw answers do not follow** - a RawAnswer is what the author said
  about the old clause; carrying it over would put unrelated Q&A on the new
  clause's page. A field's `value` is the extracted literal text, and may carry;
- **The old clause does not follow** - copying is not moving; the old
  interpretation stays exactly as it was, lossless and repeatably inheritable.

Validation and persistence are two separate functions: the route layer calls
`check()` first to get the refusal reason, and `inherit()` only acts once it
passes. The model takes no part in this flow - inheritance is a pure code copy.
"""
from framework_reader.interpret.model import (
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    InterviewRecord,
)
from framework_reader.query.api import QueryAPI


class InheritDenied(Exception):
    """Inheritance was refused. `message` is user-facing text, rendered straight onto the page."""


def check(old_id: str, new_id: str, store, api: QueryAPI) -> None:
    """Three hard checks: the edge exists, the old side has an interpretation, the new side has none."""
    successors = {v.control_id for v in api.superseded_by(old_id)}
    if new_id not in successors:
        raise InheritDenied(
            f"{old_id} and {new_id} have no supersession relation; cannot inherit"
        )
    if not store.exists(old_id):
        raise InheritDenied(f"{old_id} has no interpretation; nothing to inherit")
    if store.exists(new_id):
        raise InheritDenied(f"{new_id} already has an interpretation; inheriting would overwrite it - not supported yet")


def inherit(old_id: str, new_id: str, store, api: QueryAPI) -> Interpretation:
    """Copy, persist, and return the interpretation now sitting under the new clause."""
    check(old_id, new_id, store, api)
    old = store.load(old_id)
    interp = Interpretation(
        control_id=new_id,
        locale=old.locale,
        state=InterpretationState.DRAFT,
        fields={name: field.model_copy(deep=True)
                for name, field in old.fields.items()},
        interview=InterviewRecord(),
        provenance=InterpretationProvenance(
            drafter=old.provenance.drafter,
            extractor=old.provenance.extractor,
            inherited_from=old_id,
        ),
    )
    store.save(interp)
    return interp
