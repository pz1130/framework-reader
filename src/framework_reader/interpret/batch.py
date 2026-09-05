"""Batch drafting. W2 spec §2.1: drafting and interviewing are separate; the interview phase never waits on a model."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from framework_reader.interpret.drafter import draft_fields, draft_full_fields
from framework_reader.interpret.golden import few_shot_examples
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Field,
    Interpretation,
    InterviewRecord,
    InterpretationProvenance,
    ModelRef,
)
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import LLMClient
from framework_reader.query.api import QueryAPI


class DraftFailure(BaseModel):
    control_id: str
    reason: str


class DraftReport(BaseModel):
    """One control out of 106 hits a model hiccup - it must not drag the other 105 down with it."""

    written: list[str] = []
    failed: list[DraftFailure] = []


def _is_empty(value) -> bool:
    return value in (None, "", [], {})


def _keep_human_content(
    store: InterpretationStore, control_id: str, fresh: dict[str, Field],
    blanks_only: bool = False,
) -> tuple[dict[str, Field], InterviewRecord]:
    """Re-drafting overwrites only what AI wrote. The author's verbatim answers and hand-edited fields always survive.

    The gate points the right way (W2 spec §6: nothing the author said may be lost), but the granularity
    belongs on the field, not on the whole operation going on strike - with that, a single control holding

    `blanks_only`: fill blanks only. Every field that already has words is untouched, whoever wrote it -
    "fill the blanks" means "do not touch what I have read", including AI drafts the user accepted.
    """
    if not store.exists(control_id):
        return fresh, InterviewRecord()
    previous = store.load(control_id)
    merged = dict(fresh)
    for name, field in previous.fields.items():
        if blanks_only:
            if not _is_empty(field.value):
                merged[name] = field
        elif field.basis is Basis.PRACTITIONER and field.value is not None:
            merged[name] = field          # authored or edited by the author: untouched
    return merged, previous.interview


def own_examples(store, framework_id: str, exclude: str, limit: int = 3) -> list:
    """Use **this organization's confirmed controls** as the examples.

    This is where "user and AI interpret together" gets real: the controls they confirmed carry their
    company's tone and granularity, which the model should learn from - the golden samples teach generic
    concreteness, these teach "what we call this here, and how fine we split it".

    Confirmed ones only. An unconfirmed control may itself be model output - learning from it is the
    """
    from framework_reader.interpret.model import InterpretationState

    out = []
    for interp in store.by_state(InterpretationState.CONFIRMED):
        if interp.control_id == exclude:
            continue
        if not interp.control_id.startswith(f"{framework_id}:"):
            continue
        if all(_is_empty(f.value) for f in interp.fields.values()):
            continue
        out.append(interp)
        if len(out) >= limit:
            break
    return out


def _has_blank(store, control_id: str) -> bool:
    if not store.exists(control_id):
        return True
    return any(_is_empty(f.value) for f in store.load(control_id).fields.values())


def _examples_for(store, framework_id: str, control_id: str, *, own: bool) -> list:
    """The organization's confirmed controls come first; handwritten golden samples top up to three.

    Golden samples teach generic granularity; the organization's own examples teach its own voice. Not
    but the organization's own examples are closer to home, so they come first.
    """
    mine = own_examples(store, framework_id, control_id) if own else []
    if len(mine) >= 3:
        return mine
    return mine + few_shot_examples(exclude=control_id)[: 3 - len(mine)]


def _our_practice_for(documents, api, control) -> list[str]:
    """The passages in the organization's own policies relevant to this control. Empty when no documents uploaded.

    The retrieval query is built from "title + control body" - never the control number: no internal
    policy cites foreign control numbers, so searching by one retrieves nothing.
    """
    if documents is None:
        return []
    query = " ".join(filter(None, [control.label, api.control_body(control.id) or ""]))
    return documents.excerpts(query)


def _empty_differentiating() -> dict[str, Field]:
    return {n: Field(value=None, basis=Basis.PRACTITIONER) for n in DIFFERENTIATING_FIELDS}


def draft_all(
    store: InterpretationStore,
    api: QueryAPI,
    client: LLMClient,
    *,
    framework_id: str,
    model: str,
    prompt_version: str,
    provider: str,
    jobs: int = 4,
    force: bool = False,
    only: list[str] | None = None,
    full: bool = False,
    failure_dir: Path | None = None,
    fill_blanks: bool = False,
    documents=None,
) -> "DraftReport":
    """When `only` is non-empty, draft just those controls - before a vendor is chosen, nobody owes 106 drafts.

    `fill_blanks`: fill blanks only. The target widens from "controls without interpretations" to
    "controls with empty fields", and on write any field that already has words is untouched. A user
    who wrote two sentences and wants AI to fill the rest takes exactly this path - otherwise the

    `documents`: companion documents the user uploaded (`DocumentStore`). When given, each control
    carries the most relevant passages from the organization's own policies (design §8 S5) - without
    """
    from framework_reader.interpret.grounding import catalog_prose, grounding_lines
    from framework_reader.schema.entities import LicenseTier

    leaves = list(api.list_controls(framework_id, active_only=True, leaf_only=True))
    view = api.get_framework(framework_id)
    # Tier A source text is public domain and may go straight to the model; other frameworks' source
    # text is neither in the library nor ever allowed out to the network - there the label is our own
    embeddable = view is not None and view.tier == LicenseTier.A_EMBEDDABLE
    # User-imported frameworks: the body is the user's own company document, drafted with the
    # a different universe from the copyrighted Tier C/D standard texts. Main spec §7.3.5
    own = view is not None and view.tier == LicenseTier.U_USER
    prose = {} if embeddable else catalog_prose()
    if only:
        missing = sorted(set(only) - {c.id for c in leaves})
        if missing:
            raise ValueError(f"These controls are not active leaves of {framework_id}: {missing}")
        leaves = [c for c in leaves if c.id in set(only)]
    if fill_blanks:
        targets = [c for c in leaves if _has_blank(store, c.id)]
    else:
        targets = [c for c in leaves if force or not store.exists(c.id)]

    def one(control) -> str:
        # Per-call connection: sqlite3 default check_same_thread forbids sharing.
        worker_api = QueryAPI(api._db_path)
        try:
            neighbors = [
                n.control_id for n in worker_api.neighbors(control.id, exportable_only=True)
                if n.control_id.startswith("NIST-800-53-R5:")
            ]
            if full:
                # Route B: all seven fields written in one pass, all marked inferred. Main spec §5
                fields = draft_full_fields(
                    client, control_id=control.id,
                    outcome=(
                        control.label if embeddable
                        else worker_api.control_body(control.id) if own
                        else ""
                    ),
                    label="" if embeddable else control.label,
                    grounding=(
                        [] if embeddable
                        else grounding_lines(worker_api, control.id, prose)
                    ),
                    practice=_our_practice_for(documents, worker_api, control),
                    neighbors=neighbors, model=model,
                    # The target control's own handwritten sample must be excluded - that is copying the answer, not learning granularity
                    examples=_examples_for(
                        store, framework_id, control.id, own=own
                    ),
                    **({"failure_dir": failure_dir} if failure_dir is not None else {}),
                )
            else:
                fields = draft_fields(
                    client, control_id=control.id, outcome=control.label,
                    neighbors=neighbors, model=model,
                )
                fields.update(_empty_differentiating())
            fields, interview = _keep_human_content(
                store, control.id, fields, blanks_only=fill_blanks
            )
            store.save(Interpretation(
                control_id=control.id,
                fields=fields,
                interview=interview,
                provenance=InterpretationProvenance(
                    drafter=ModelRef(
                        provider=provider, model=model, prompt_version=prompt_version
                    )
                ),
            ))
            return control.id
        finally:
            worker_api._conn.close()

    def guarded(control) -> tuple[str, str | None]:
        """A single failure is booked, not a table-flip. Failed controls are not written; re-run (without --force) to fill them."""
        try:
            return one(control), None
        except Exception as exc:
            return control.id, f"{type(exc).__name__}: {exc}"

    if jobs <= 1:
        results = [guarded(c) for c in targets]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(guarded, targets))

    report = DraftReport()
    for control_id, error in sorted(results):
        if error is None:
            report.written.append(control_id)
        else:
            report.failed.append(DraftFailure(control_id=control_id, reason=error))
    return report
