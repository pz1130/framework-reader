"""What an interpretation looks like when a human reads it.

This is the **only** place that decides how an interpretation is presented - `fr show` and the
blind test's product variant share it. Written twice, the two copies slowly drift apart.

It used to live in blindtest/variants.py. After the 2026-08-22 self-use downgrade the blind test is
no longer a gate, and the main query path must not depend back on it. Main spec §7.3.1
"""
from pydantic import BaseModel

from framework_reader.interpret.model import Interpretation


FIELD_LABELS = (
    ("intent", "What it defends against"),
    ("plain_zh", "Plain words"),
    ("practice", "How to implement"),
    ("evidence", "What serves as evidence"),
    ("common_myth", "Common misconceptions"),
    ("auditor_asks", "What auditors will probe"),
    ("regional_note", "Regional notes"),
)

_MAPPING_LABEL = "Mappings to other frameworks"


# Relations and levels are rendered as words for the judges. Printing raw `related` / `L1_OFFICIAL`
# shoves internal enum names at the reader - and "related" should not appear in reader-facing material.
_RELATION_LABELS = {
    "equivalent": "Equivalent",
    "subset": "Covered by",
    "superset": "Covers",
    "related": "Related",
    "conflicts": "Conflicts",
}
_LEVEL_LABELS = {
    "L1_OFFICIAL": "Official mapping",
    "L2_PUBLIC": "Published crosswalk",
    "L3_CONFIRMED": "Human confirmed",
}


class MappingRef(BaseModel):
    """One exportable mapping edge, carrying everything display needs.

    Assembled by the caller from QueryAPI - variants never touch the database, so they test detached.
    """

    control_id: str
    label: str
    framework: str
    relation: str
    source: str
    level: str


def _short_id(control_id: str) -> str:
    """`NIST-800-53-R5:AC-4` -> `AC-4`. The framework name is already in the section heading."""
    return control_id.split(":", 1)[-1]


def render_mappings(refs: list[MappingRef]) -> str:
    """Which other frameworks' controls this one maps to, and where that mapping comes from. Main spec §7.3 second pass bar

    The provenance in question is the **mapping's**, not the fields' - spec §4 lists basis / inferred as
    leak words; field-level grounding was never allowed into a packet anyway.
    """
    if not refs:
        return ""

    groups: dict[tuple[str, str], list[MappingRef]] = {}
    for ref in refs:
        groups.setdefault((ref.framework, ref.relation), []).append(ref)

    lines = [f"**{_MAPPING_LABEL}**"]
    for (framework, relation), items in groups.items():
        shown = ", ".join(f"{_short_id(i.control_id)} {i.label}" for i in items)
        relation_zh = _RELATION_LABELS.get(relation, relation)
        lines.append(f"- {framework} ({relation_zh}, {len(items)} controls): {shown}")

    sources = dict.fromkeys(
        f"{ref.source} ({_LEVEL_LABELS.get(ref.level, ref.level)})" for ref in refs
    )
    lines.append(f"- Mapping sources: {'; '.join(sources)}, each traceable")
    return "\n".join(lines)


def render_interpretation(
    interp: Interpretation, mappings: list[MappingRef] | None = None
) -> str:
    lines: list[str] = []
    for name, label in FIELD_LABELS:
        field = interp.fields.get(name)
        if field is None or field.value in (None, "", [], {}):
            continue          # empty fields simply do not appear - no None/null shown
        lines.append(f"**{label}**")
        value = field.value
        if isinstance(value, dict):
            for level, body in sorted(value.items()):
                lines.append(f"- {level} - {body}")
        elif isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        else:
            lines.append(str(value))
        lines.append("")

    block = render_mappings(mappings or [])
    if block:
        lines.append(block)
    return "\n".join(lines).strip()
