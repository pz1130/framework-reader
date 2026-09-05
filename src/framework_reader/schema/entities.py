"""Core entities. spec §3.1"""
from enum import Enum

from pydantic import BaseModel, model_validator


class LicenseTier(str, Enum):
    """Corpus licensing tiers. spec §4.1"""

    A_EMBEDDABLE = "A"       # fully embeddable (public domain)
    B_NO_REDIST = "B"        # freely obtainable but not redistributable
    C_PURCHASE = "C"         # must be purchased
    D_NO_COMMERCIAL = "D"    # no commercial use at all
    # Frameworks the user imported themselves (internal company policies). Never enter the content
    # pack we publish, and are never distributed by us; drafting may use their own text and key. Main spec §7.3.5
    U_USER = "U"


class ControlStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class SupersedeRelation(str, Enum):
    """Where a superseded control went. OSCAL catalog uses hyphens, CSF underscores; normalised here."""

    INCORPORATED_INTO = "incorporated_into"   # content merged into another (possibly several)
    MOVED_TO = "moved_to"                     # same content, new number


class Framework(BaseModel):
    id: str
    name: str
    version: str
    tier: LicenseTier
    source_url: str
    license_note: str


class FrameworkControl(BaseModel):
    id: str
    framework_id: str
    parent_id: str | None = None
    label: str
    label_is_original: bool
    framework_tier: LicenseTier
    status: ControlStatus = ControlStatus.ACTIVE

    @model_validator(mode="after")
    def _forbid_original_label_outside_tier_a(self) -> "FrameworkControl":
        # Only Tier A (public domain) may reuse the official title verbatim. spec §4.1
        if self.label_is_original and self.framework_tier is not LicenseTier.A_EMBEDDABLE:
            raise ValueError(
                f"label_is_original=True is only allowed for Tier A; {self.framework_id} is "
                f"Tier {self.framework_tier.value}; label must be self-written"
            )
        return self


class Supersession(BaseModel):
    """The relation between a superseded control and where it went. spec §8②

    Many-to-many: one control can be split into several, and one can absorb several old numbers - so it
    cannot be a single-valued field on FrameworkControl.
    """

    old_id: str
    new_id: str
    relation: SupersedeRelation

    @model_validator(mode="after")
    def _reject_self_reference(self) -> "Supersession":
        if self.old_id == self.new_id:
            raise ValueError("old_id and new_id must not be the same")
        return self


class UnifiedControl(BaseModel):
    """The hub layer. spec §3.2①: content copied 1:1 from CSF 2.0; schema fully independent."""

    id: str
    label: str
    locale: str
