"""The Statement of Applicability (SoA). Main spec §7.3.3

The SoA is the document an ISO 27001 certification submits. Two hard rules:

1. **All 93 controls, none missing.** Unfilled ones are marked TBD in the table - they may not
   quietly disappear. A missing row is not "no problem"; it is "nobody looked".
2. **Derived edges never enter this table.** At 17% accuracy they only deserve to be entry-time hints. §3.3
"""
import csv
import io

from pydantic import BaseModel

from framework_reader.assess.store import Assessment

HEADERS = ("Control", "Name", "Applicability", "Reason if N/A", "Implementation status", "Notes / evidence")
PENDING = "TBD"


class SoaRow(BaseModel):
    control_id: str
    label: str
    applicable: bool | None = None      # None = not yet assessed
    reason: str = ""
    status: str = ""
    note: str = ""


def build_soa(
    controls: list[tuple[str, str]], entries: list[Assessment]
) -> list[SoaRow]:
    seen = {e.control_id: e for e in entries}
    rows = []
    for control_id, label in controls:
        entry = seen.get(control_id)
        if entry is None:
            rows.append(SoaRow(control_id=control_id, label=label))
            continue
        rows.append(SoaRow(
            control_id=control_id, label=label,
            applicable=entry.applicable, reason=entry.reason,
            status=entry.status, note=entry.note,
        ))
    return rows


def _cells(row: SoaRow) -> tuple[str, ...]:
    if row.applicable is None:
        applicable = PENDING
    else:
        applicable = "Applicable" if row.applicable else "Not applicable"
    return (
        row.control_id.split(":", 1)[-1],
        row.label,
        applicable,
        row.reason,
        row.status or (PENDING if row.applicable else ""),
        row.note,
    )


def render_soa_markdown(rows: list[SoaRow]) -> str:
    def escape(text: str) -> str:
        # An unescaped pipe would widen this row's column count and misalign the whole table.
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(HEADERS) + " |",
        "|" + "|".join(["---"] * len(HEADERS)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape(c) for c in _cells(row)) + " |")
    return "\n".join(lines)


def render_soa_csv(rows: list[SoaRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(_cells(row))
    return buffer.getvalue()


def fill_hints(api, control_id: str, limit: int = 4) -> list[str]:
    """Entry-time hints for the SoA: which controls in other frameworks this one corresponds to.

    **Entry-time only; never reaches the deliverable.** Official mappings (L1) and derived edges (L2)
    are labelled separately - derived edges sampled 17% correct in R7: fine for recall, not as evidence. Main spec §3.3

    Same title keeps one entry: every 800-53 family has an "-1 Policy and Procedures"; without
    without deduplication those four hints would all be the same sentence.
    """
    neighbors = sorted(api.neighbors(control_id), key=lambda n: not n.exportable)
    seen: set[str] = set()
    hints: list[str] = []
    for neighbor in neighbors:
        if neighbor.label in seen:
            continue
        seen.add(neighbor.label)
        kind = "Mapped (official mapping)" if neighbor.exportable else "Hint (inferred, not citable)"
        hints.append(f"{kind}: {neighbor.control_id}  {neighbor.label}")
        if len(hints) >= limit:
            break
    return hints
