"""When the header cannot be recognized deterministically, let the model look at
what the sheet actually looks like. See the 2026-08-25 AI import design.

**This path is taken only when deterministic parsing fails.** Header on the first
row, column names recognizable - that path is free, instant, and cannot get it
wrong; there is no reason to swap it for a model call.

**Same principle as the document path: the model points, the code takes the text.**
It returns row and column numbers, not content, so it physically cannot alter a
character of the user's table.

The second kind of answer is "this is not a table at all": a policy pasted into
Excel, one paragraph per row, with no id/title columns to speak of. Forcing a column
mapping onto it yields a pile of garbage clauses; that input belongs to the document
pipeline.
"""
import json
import re
from dataclasses import dataclass

from framework_reader.userframework.outline import Span, fill_gaps

# The sample shows the model structure, not content. Cells are truncated to this length.
_CELL_SAMPLE = 40
_SAMPLE_ROWS = 15
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


@dataclass(frozen=True)
class TableShape:
    kind: str                       # table | document
    sheet: str = ""                 # sheet name. Empty = only one sheet, or unspecified
    header_row: int = 0             # 1-based
    id_col: int = -1                # 0-based
    label_col: int = -1
    parent_col: int | None = None
    body_col: int | None = None
    why: str = ""


def sample_for_model(rows: list[list[str]], limit: int = _SAMPLE_ROWS) -> str:
    """The first rows, with row and column numbers. A model that must point at positions has to be able to see them."""
    out = []
    for number, row in enumerate(rows[:limit], start=1):
        cells = " │ ".join(
            f"C{index}:{str(cell).strip()[:_CELL_SAMPLE]}"
            for index, cell in enumerate(row) if str(cell).strip())
        out.append(f"R{number} {cells}")
    return "\n".join(out)


def sample_sheets(sheets: list[tuple[str, list[list[str]]]],
                  limit: int = _SAMPLE_ROWS) -> str:
    """A sample of the whole workbook. **Every sheet is included** - "instructions
    first, real table last" is the most common layout; showing only the first sheet
    makes the model take the blame for our oversight.
    """
    return "\n\n".join(
        f'=== Sheet "{name}" ===\n{sample_for_model(rows, limit)}'
        for name, rows in sheets
    )


def sheets_to_text(sheets: list[tuple[str, list[list[str]]]]) -> str:
    """When treating it as a document, flatten the whole workbook into text.

    Sheet names are kept - an "appendix" and the "main body" are different things;
    flatten them together and they become indistinguishable.
    """
    return "\n".join(
        (f"{name}\n{rows_to_text(rows)}" if name else rows_to_text(rows))
        for name, rows in sheets
    )


def rows_to_text(rows: list[list[str]]) -> str:
    """When treating it as a document, flatten every cell into text in reading order.

    Empty cells are skipped, but **never a whole row** - merged cells often have text
    only in the first column.
    """
    lines = []
    for row in rows:
        for cell in row:
            text = str(cell).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def parse_shape(raw: str) -> tuple[TableShape | None, str]:
    """Parse the model's reply. **Never raises** - the caller is an upload request."""
    text = _FENCE.sub("", (raw or "").strip())
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None, "The model reply is not JSON."
    if not isinstance(payload, dict):
        return None, "The model reply is not an object."
    kind = str(payload.get("kind", "")).strip()
    sheet = str(payload.get("sheet") or "").strip()
    if kind == "document":
        return TableShape(kind="document", sheet=sheet,
                          why=str(payload.get("why", "")).strip()), ""
    if kind != "table":
        return None, f"The model said it is '{kind or 'unknown'}', which is neither of the two."

    def index(key: str, required: bool):
        value = payload.get(key)
        if value is None or value == "":
            return None if not required else "missing"
        try:
            return int(value)
        except (TypeError, ValueError):
            return "missing"

    header_row = index("header_row", True)
    id_col = index("id_col", True)
    label_col = index("label_col", True)
    if "missing" in (header_row, id_col, label_col):
        return None, "The model did not say which row the header is on, or which columns hold numbers and titles."
    parent_col = index("parent_col", False)
    body_col = index("body_col", False)
    return TableShape(
        kind="table", sheet=sheet,
        header_row=header_row, id_col=id_col, label_col=label_col,
        parent_col=None if parent_col == "missing" else parent_col,
        body_col=None if body_col == "missing" else body_col,
    ), ""


def validate_shape(shape: TableShape, rows: list[list[str]],
                   sheet_names: list[str] | None = None
                   ) -> tuple[TableShape | None, str]:
    """Trust the model on nothing. Out-of-range, the same column used twice, no data
    below the header, a named sheet that does not exist - all rejected."""
    if sheet_names is not None and shape.sheet and shape.sheet not in sheet_names:
        return None, f'The model said the data is in sheet "{shape.sheet}" but this workbook has no such sheet.'
    if shape.kind == "document":
        return shape, ""
    if not 1 <= shape.header_row <= len(rows):
        return None, (f"The model said the header is on row {shape.header_row}, "
                      f"but this sheet only has {len(rows)} rows.")
    if shape.header_row >= len(rows):
        return None, f"The model said the header is on row {shape.header_row}, but there is not a single data row below it."
    width = max((len(row) for row in rows), default=0)
    used = [shape.id_col, shape.label_col, shape.parent_col, shape.body_col]
    for column in used:
        if column is not None and not 0 <= column < width:
            return None, f"The model pointed to column {column} but this sheet only has {width} columns."
    named = [c for c in used if c is not None]
    if len(named) != len(set(named)):
        return None, "The model pointed both things at the same column."
    return shape, ""


def to_draft(rows: list[list[str]],
             shape: TableShape) -> tuple[str, list[Span]]:
    """Read cell by cell using the model's indexes and assemble the (source snapshot,
    clause boundaries) the preview page needs.

    **The body snapshot is exactly the concatenation of the body cells**, and each
    clause's line range lands precisely on its own rows. Preview and storage then both
    go through `slice_lines`, same mechanism as the document path - the body comes
    verbatim from the original sheet with no second copy in between.
    """
    def cell(row: list[str], column: int | None) -> str:
        if column is None or column >= len(row):
            return ""
        return str(row[column]).strip()

    lines: list[str] = []
    spans: list[Span] = []
    for row in rows[shape.header_row:]:
        ref = cell(row, shape.id_col)
        label = cell(row, shape.label_col)
        body = cell(row, shape.body_col)
        if not ref and not label and not body:
            continue                    # the whole row is empty
        # Has text but no number and no title (a trailing "End of document" line, a
        # remarks row): **kept, never dropped silently**. The preview page leaves it
        # unchecked by default because the title is empty; a person sees it at a
        # glance and decides whether to delete it.
        body_lines = body.splitlines() if body else []
        start = len(lines) + 1
        lines.extend(body_lines)
        spans.append(Span(
            ref=ref, label=label,
            parent=cell(row, shape.parent_col) or None,
            start=start, end=start + len(body_lines) - 1,
        ))
    # Sheets have empty cells too. Fill in numbers and titles, for the same reason as
    # the document path.
    return "\n".join(lines), fill_gaps(spans, lines)
