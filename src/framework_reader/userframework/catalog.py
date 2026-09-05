"""Harvest "letter-prefixed clause numbers" from the source text. See the
2026-08-29 NIST.AI.100-1 import

Framework PDFs often carry two numbering schemes: section numbers (5.1 Govern)
and clause numbers (GOVERN 1.1, PR.AA-01, AC-2, A.5.1, Article 9). Clause
numbers are frequently laid out as a table. Following the section numbers, the
model swallows the whole table into one clause - NIST.AI.100-1's 5.1 came out
as a single clause of 7223 characters with 19 GOVERN subcategories buried
inside.

**No special-casing by framework name.** The test is shape: a line starts with
"letter prefix + number", and enough of them accumulate across the document
before it counts as a clause table. A company policy's 5.1 / 3.2 has no letter
prefix, so this code never touches it.

However the model cuts, this step runs before validation and splits the table
rows apart using the source line numbers. Body text is still cut from the
source by line number, never through the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from framework_reader.userframework.outline import Problem, Span

# Below this count, nothing is harvested. An occasional "see Article 9"
# reference in body text does not make a table.
MIN_HARVEST = 3

# Layout words: TABLE 1, FIGURE 5, PAGE 22 look like clause numbers but are not.
_LAYOUT = {
    "table", "figure", "fig", "page", "section", "chapter", "part",
    "appendix", "note", "notes", "box", "step", "phase", "volume",
    "version", "level", "type", "class", "group", "see", "the",
    "list", "item", "row", "column",
}

# Ordered "most specific pattern first". Capture group 1 = the number,
# group 2 = the rest of the line.
_PATTERNS = (
    re.compile(r"^([A-Z]{2}\.[A-Z]{2}-\d{2})\W*(.*)$"),          # GV.OC-01
    re.compile(r"^([A-Z]{2}-\d+\s*\(\d+\))\W*(.*)$"),            # AC-2(1)
    re.compile(r"^([A-Z]{2}-\d+)\W*(.*)$"),                      # AC-2
    re.compile(r"^([A-Z]{2}\d+\.\d+)\W*(.*)$"),                  # CC6.1
    re.compile(r"^(A\.\d+\.\d+(?:\.\d+)?)\W*(.*)$"),             # A.5.1
    re.compile(r"^(Article\s+\d+(?:\s*\(\d+\))?)\W*(.*)$", re.I),  # Article 9
    re.compile(r"^((?:Control|Safeguard)\s+\d+(?:\.\d+)*)\W*(.*)$", re.I),
    re.compile(r"^([A-Z]{3,}\s+\d+(?:\.\d+)*)\W*(.*)$"),         # GOVERN 1.1
)

_CHROME = re.compile(
    r"^(categories(\s+subcategories)?|continued on next page|"
    r"page\s+\d+|table\s+\d+\b|fig\.?\s*\d+|figure\s+\d+)",
    re.I,
)

# A new chapter / a new table (not Continued). Past this point it is no longer
# the previous clause's body. Multi-word headings like "6. AI RMF Profiles" must
# be recognized too - `\\S{1,30}` eats only one word, which would let the last
# clause swallow the next chapter's introduction.
_SECTION_BREAK = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+[章节条]|"
    r"\d+(?:\.\d+)*[、.．]?\s+\S.{0,40}$|"
    r"appendix\s+[a-z]\b)",
    re.I,
)

# "Word + number" shapes like GOVERN 1.1 easily catch false positives such as
# "RMF 1.0" at a line start. The same prefix needs at least two hits to count as
# a table; AC-2 / Article 9 / A.5.1 are narrower shapes, so a single hit is taken.
_GENERIC_WORD_NUM = re.compile(r"^[A-Z]{3,}\s+\d")
_NAMED_WORD_NUM = re.compile(r"^(Article|Control|Safeguard)\s", re.I)

_LABEL_MAX = 80


@dataclass(frozen=True)
class CatalogEntry:
    ref: str
    label: str
    parent: str | None
    start: int
    end: int

    def to_span(self) -> Span:
        return Span(
            ref=self.ref, label=self.label, parent=self.parent,
            start=self.start, end=self.end,
            ref_from="original",
            label_from="original" if self.label else "derived",
        )


def parse_catalog_line(line: str) -> tuple[str, str] | None:
    """If the line starts with a clause number, return (number, rest of the line); otherwise None."""
    text = (line or "").strip()
    if not text:
        return None
    for pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        ref = re.sub(r"\s+", " ", match.group(1)).strip()
        rest = match.group(2).strip().lstrip(":.-–— ").strip()
        head = ref.split()[0].rstrip(".").lower()
        if head in _LAYOUT:
            continue
        return ref, rest
    return None


def find_catalog_entries(lines: list[str]) -> list[CatalogEntry]:
    """Scan the whole document. Each entry runs from its own number line to just
    before the next number, stopping at a new chapter or table title, with the
    trailing layout lines ("Continued on next page") peeled off.
    """
    hits: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        parsed = parse_catalog_line(line)
        if parsed:
            hits.append((index, parsed[0], parsed[1]))
    if not hits:
        return []
    hits = _drop_singleton_generic_families(hits)
    if not hits:
        return []
    known = {ref for _i, ref, _rest in hits}
    out: list[CatalogEntry] = []
    for pos, (start, ref, rest) in enumerate(hits):
        next_start = hits[pos + 1][0] if pos + 1 < len(hits) else len(lines) + 1
        end = _entry_end(start, next_start, lines)
        continuation = ""
        if not rest and start < end:
            continuation = lines[start].strip()  # next line, 0-based = start
        out.append(CatalogEntry(
            ref=ref,
            label=_label(rest, continuation),
            parent=_parent_ref(ref, known),
            start=start,
            end=end,
        ))
    return out


def apply_catalog(spans: list[Span],
                  lines: list[str]) -> tuple[list[Span], list[Problem]]:
    """Split the clause table into the existing cut. Too few entries and it is ignored entirely."""
    entries = find_catalog_entries(lines)
    if len(entries) < MIN_HARVEST:
        return spans, []
    catalog = [e.to_span() for e in entries]
    catalog_refs = {s.ref for s in catalog}
    catalog_starts = {s.start for s in catalog}
    kept = [
        span for span in spans
        if span.ref not in catalog_refs and span.start not in catalog_starts
    ]
    sample = "、".join(e.ref for e in entries[:3])
    problem = Problem(
        "catalog",
        f"Found {len(entries)} prefix-numbered clauses (e.g. {sample}), "
        "split by their clause numbers instead of swallowing a whole section.",
    )
    return kept + catalog, [problem]


def _entry_end(start: int, next_start: int, lines: list[str]) -> int:
    end = start
    last = min(len(lines), next_start - 1)
    for index in range(start, last + 1):
        line = lines[index - 1].strip()
        if index > start and parse_catalog_line(line):
            break
        if index > start and _is_section_break(line):
            break
        end = index
    while end > start and _is_chrome(lines[end - 1]):
        end -= 1
    return end


def _is_section_break(line: str) -> bool:
    text = line.strip()
    if not text or parse_catalog_line(text):
        return False
    # A new table title counts no matter how long ("Table 2: Categories and
    # subcategories for the MAP function." is over 40 characters). Continued is
    # the same table flowing onto the next page, not a boundary.
    if re.match(r"^table\s+\d+\s*:", text, re.I) and "continued" not in text.lower():
        return True
    if len(text) > 40:
        return False
    return bool(_SECTION_BREAK.match(text))


def _is_chrome(line: str) -> bool:
    return bool(_CHROME.match(line.strip()))


def _label(rest: str, continuation: str) -> str:
    raw = rest or continuation
    raw = re.sub(r"\s+", " ", raw).strip(" -")
    return raw[:_LABEL_MAX].rstrip(" -")


def _drop_singleton_generic_families(
    hits: list[tuple[int, str, str]],
) -> list[tuple[int, str, str]]:
    """A line-wrap leftover like "RMF 1.0" appears only once; GOVERN 1 / 1.1 / 1.2 is a table."""
    from collections import Counter

    counts = Counter(
        ref.split()[0]
        for _i, ref, _rest in hits
        if _GENERIC_WORD_NUM.match(ref) and not _NAMED_WORD_NUM.match(ref)
    )
    keep = {word for word, n in counts.items() if n >= 2}
    out = []
    for hit in hits:
        ref = hit[1]
        if _GENERIC_WORD_NUM.match(ref) and not _NAMED_WORD_NUM.match(ref):
            if ref.split()[0] not in keep:
                continue
        out.append(hit)
    return out


def _parent_ref(ref: str, known: set[str]) -> str | None:
    stripped = re.sub(r"\s*\(\d+\)\s*$", "", ref).strip()
    if stripped != ref and stripped in known:
        return stripped
    match = re.match(r"^(.+?)(?:\.|\s)(\d+)$", ref)
    if match and match.group(1) in known:
        return match.group(1)
    return None
