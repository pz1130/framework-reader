"""Mapping edges and provenance. spec §3.2③, §3.3"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ProvenanceLevel(str, Enum):
    L1_OFFICIAL = "L1_OFFICIAL"      # mappings attributed by NIST itself, official framework annexes
    L2_DERIVED = "L2_DERIVED"        # transitively derived across two L1 edges
    L2_PUBLIC = "L2_PUBLIC"          # public crosswalks licensed for derivation and commercial use
    L3_CONFIRMED = "L3_CONFIRMED"    # human-confirmed
    L4_AI = "L4_AI"                  # model guess


# The levels allowed into exports. spec §3.3
EXPORTABLE_LEVELS = frozenset(
    {ProvenanceLevel.L1_OFFICIAL, ProvenanceLevel.L2_PUBLIC, ProvenanceLevel.L3_CONFIRMED}
)


class Relation(str, Enum):
    EQUIVALENT = "equivalent"
    SUBSET = "subset"
    SUPERSET = "superset"
    RELATED = "related"
    CONFLICTS = "conflicts"


class Provenance(BaseModel):
    level: ProvenanceLevel
    source: str
    source_version: str
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    derived_via: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_level_invariants(self) -> "Provenance":
        if self.level is ProvenanceLevel.L3_CONFIRMED:
            if not self.confirmed_by or self.confirmed_at is None:
                raise ValueError("L3_CONFIRMED must record confirmed_by and confirmed_at")
        if self.level is ProvenanceLevel.L2_DERIVED and not self.derived_via:
            raise ValueError("L2_DERIVED must record intermediate nodes in derived_via")
        return self


class Mapping(BaseModel):
    from_id: str
    to_id: str
    relation: Relation
    provenance: Provenance
    note: str = ""

    @model_validator(mode="after")
    def _reject_self_loop(self) -> "Mapping":
        if self.from_id == self.to_id:
            raise ValueError("from_id and to_id must not be the same")
        return self

    @property
    def exportable(self) -> bool:
        return self.provenance.level in EXPORTABLE_LEVELS
