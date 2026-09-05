"""The interpretation schema and state machine. W2 spec §4.2, §4.3; main spec §3.4"""
import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, model_validator

# The four fields AI may draft.
DRAFTED_FIELDS = ("intent", "plain_zh", "practice", "evidence")
# The three fields AI must never draft - the reason the product charges money. W2 spec §1 D1
DIFFERENTIATING_FIELDS = ("common_myth", "auditor_asks", "regional_note")
ALL_FIELDS = DRAFTED_FIELDS + DIFFERENTIATING_FIELDS


class Basis(str, Enum):
    """The grounding of a statement. Main spec §3.4 + W2 spec §2.4"""

    QUOTE = "quote"                # grounded in a sentence of the source text, shaped like quote:<locator>
    INFERRED = "inferred"          # model inference
    PRACTITIONER = "practitioner"  # the author's practitioner experience


class InterpretationState(str, Enum):
    DRAFT = "draft"                # drafter finished; differentiating fields empty
    INTERVIEWED = "interviewed"    # raw answers stored, extraction done, not signed
    CONFIRMED = "confirmed"        # author-signed; the only state that may enter a build


FieldValue = str | list[str] | dict[str, str] | None


class Field(BaseModel):
    value: FieldValue = None
    basis: Basis


class Question(BaseModel):
    n: int
    kind: Literal["fixed", "adaptive"]
    text: str


class RawAnswer(BaseModel):
    n: int
    text: str


class InterviewRecord(BaseModel):
    questions: list[Question] = []
    raw: list[RawAnswer] = []
    # Answer numbers where not a single word made it into a field. Dropping them is allowed; vanishing silently is not.
    unplaced: list[int] = []


class ModelRef(BaseModel):
    provider: str
    model: str
    prompt_version: str


class InterpretationProvenance(BaseModel):
    drafter: ModelRef | None = None
    extractor: ModelRef | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    # Digest of the content at signing time, recomputed and compared at build. An mtime is not evidence
    # that content changed - git clone / CI checkout / branch switches all refresh it. W2 spec §4.3
    signed_digest: str | None = None
    # How many seconds this interview actually took. W2 spec §8.2: this number scopes W3.
    interview_seconds: float | None = None
    # The old control an inheritance came from. Inherited artifacts stay draft forever: the signature was the old control's and must be re-signed.
    inherited_from: str | None = None


class Interpretation(BaseModel):
    control_id: str
    locale: str = "zh-CN"
    state: InterpretationState = InterpretationState.DRAFT
    fields: dict[str, Field]
    interview: InterviewRecord = InterviewRecord()
    provenance: InterpretationProvenance = InterpretationProvenance()

    @model_validator(mode="after")
    def _check_invariants(self) -> "Interpretation":
        missing = set(ALL_FIELDS) - set(self.fields)
        extra = set(self.fields) - set(ALL_FIELDS)
        if missing or extra:
            raise ValueError(f"field set must be exactly the seven fields; missing {sorted(missing)}, extra {sorted(extra)}")

        # Route B (2026-08-20): all seven fields may be AI-written, marked inferred; fields the
        # author wrote or edited are practitioner. Both are legal - basis records who wrote it. Main spec §5
        # But quote is not legal: the source text contains no "misconceptions", "auditor questions",
        # or "regional differences" - marking quote is either a modelling error or a fabricated citation.
        for name in DIFFERENTIATING_FIELDS:
            if self.fields[name].basis is Basis.QUOTE:
                raise ValueError(
                    f"{name} cannot be grounded in the original text; basis must not be quote"
                )

        if self.state is InterpretationState.CONFIRMED:
            signer = (self.provenance.confirmed_by or "").strip()
            if not signer or self.provenance.confirmed_at is None:
                raise ValueError("confirmed must record confirmed_by and confirmed_at")
            if signer.startswith("ai:") or signer.startswith("model:"):
                raise ValueError(f"AI cannot sign: confirmed_by={signer} (main spec §5)")
        return self


def fields_digest(interp: "Interpretation") -> str:
    """What the signature covers: control id, locale, the seven fields, and the author's verbatim answers.

    Provenance is excluded - so backfilling elapsed time later cannot invalidate a signature.
    """
    payload = interp.model_dump(mode="json")
    canonical = {
        "control_id": payload["control_id"],
        "locale": payload["locale"],
        "fields": payload["fields"],
        "interview": payload["interview"],
    }
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
