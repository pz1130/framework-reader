"""Split a continuous body of text into clauses. See the 2026-08-25 AI import design.

**The model only marks boundaries; the body is cut here from the source by line
number.** Let the model emit body text directly and it will silently polish
"disable the account on the departure date" into "the account must be disabled
promptly when an employee departs" - the two say roughly the same thing, but
everything downstream - the drafted interpretation, the self-assessment evidence,
the gap report - is based on that body text, and it is no longer the company's own
words, and nobody can tell which characters are original and which were casually
edited along the way. Design §1.1

Model output is treated as untrusted input throughout: parsing, validation, and
merging all happen in code.
"""
import re
from dataclasses import dataclass

# Line-number width. Four digits reaches 9999 lines; a 200-page policy is about 6000 lines.
_WIDTH = 4

# If the model emitted any of these keys, it is writing body text instead of marking boundaries.
_BODY_KEYS = {"body", "text", "content", "正文"}

# The "fits in one shot" test is a fixed conservative threshold, **not the model's
# real context** - the catalog API returns model ids only, never context lengths
# (model catalog design §1), so we have no way of knowing. Getting it wrong costs
# one extra chunk, not a failure, so err on the small side. No tokenizer:
# pulling in a dependency for an "err small" judgment is not worth it.
ONE_SHOT_MAX_CHARS = 40000


@dataclass(frozen=True)
class Span:
    """The boundary of one clause. **No body** - body text is always cut from the source.

    The model-facing JSON uses `from` / `to`; this uses `start` / `end`, because
    `from` is a Python keyword. Converting between the two names happens in exactly
    one place, `parse_outline()`.
    """

    ref: str
    label: str
    parent: str | None
    start: int          # 1-based, inclusive
    end: int            # 1-based, inclusive
    # Whether this number/title exists in the source or was filled in. **"You must
    # be able to tell who wrote what"** is the foundation of this product - the same
    # rule as the "AI draft" marking on the clause page.
    ref_from: str = "original"      # original | derived
    label_from: str = "original"


@dataclass(frozen=True)
class Problem:
    """A message that can be rendered straight onto the preview page."""

    kind: str           # out_of_range | overlap | bad_parent | not_json | has_body | uncovered | catalog | snapped
    detail: str


def line_count(text: str) -> int:
    return len(text.splitlines())


def numbered(text: str) -> str:
    """The copy the model sees: every line pinned with a line number."""
    return "\n".join(
        f"{index:0{_WIDTH}d}| {line}"
        for index, line in enumerate(text.splitlines(), start=1)
    )


def slice_lines(text: str, start: int, end: int) -> str:
    """Cut lines start through end (1-based, both ends inclusive). **Verbatim, no
    processing of any kind.**

    Indentation and full-width spaces within a line are always preserved: that is the
    policy exactly as written, not formatting noise.

    Clamp out-of-range values instead of raising. Out-of-range was supposed to be
    stopped by `validate()`; if one truly leaks through, the exception kills a
    background thread while the user stares at a page stuck on "splitting" forever.
    """
    lines = text.splitlines()
    lo = max(1, start)
    hi = min(len(lines), end)
    if lo > hi:
        return ""
    return "\n".join(lines[lo - 1:hi])


_FENCE = re.compile(r"^\s*```(?:json)?\s*$|^\s*```(?:json)?\s*$", re.MULTILINE)
# Models with a reasoning trace, like MiniMax-M3, stuff their thinking inside
# <think>...</think>. We do not need the thinking - brackets inside it would break
# the outermost-array matching below, so strip it all away first.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# When the thinking burns through the tokens, ``</think>`` never appears. Same rule as probe.py.
_THINK_UNCLOSED = re.compile(r"<think>.*$", re.DOTALL)


def _spoken(raw: str) -> str:
    """Strip the chain of thought, keeping only what the model actually said out loud.

    A closed ``<think>...</think>`` is stripped first; a leftover unclosed ``<think>``
    means there is no body after it at all (observed exactly this way when MiniMax
    split NIST.AI.100-1), so the whole stretch is discarded as scratch paper.
    """
    text = _THINK.sub("", raw or "")
    return _THINK_UNCLOSED.sub("", text)


def _raw_for_debug(raw: str) -> str:
    """Compress the model's raw reply into a form that fits inside a problems entry.

    **This is the one place that preserves the original appearance for the next
    debugging session.** Problem details are otherwise machine-generated copy - but
    for a failure like "the model did not follow the format", whoever picks this up
    next **must be able to see what the model actually replied**, otherwise they are
    just guessing blindly.

    ``<think>`` tags are stripped first - minimax-family models always stuff their
    thinking in there, and it must not be shown to the user as body text. Collapsed
    to one line because problems render as ``<li>`` in the UI; a multi-line string
    would carry unescaped newlines that are hard to handle. Newlines from the
    original are all replaced with U+23CE.
    """
    s = _spoken(raw).replace("\n", "⏎").strip()
    return s[:300] + ("..." if len(s) > 300 else "")


def _balanced_from(text: str, start: int,
                   open_ch: str = "[", close_ch: str = "]") -> str | None:
    """From the opening bracket at ``text[start]``, match through to its closing bracket.

    Brackets inside strings do not count. Returns None when they fail to match
    (truncated output, or a bracket inside thinking that never closed).
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _salvage_objects(raw: str) -> tuple[list[dict], bool]:
    """Salvage the objects that did get finished out of an unfinished reply.

    Both kinds of half-reply have been seen in the wild:
    - truncated array: ``[{...}, {...}, {"ref":``
    - no array at all: one ``{...}`` per line concatenated, the last one cut off
      (NIST.AI.100-1 yet again: it replied Framing Risk / 1.1 / 1.2 and got judged
      not_json)
    """
    import json

    text = _spoken(_FENCE.sub("", raw or "").strip())
    if "{" not in text:
        text = _FENCE.sub("", raw or "").strip()
        text = _spoken(text) or text
    bracket = text.find("[")
    brace = text.find("{")
    if brace == -1:
        return [], False
    in_array = bracket != -1 and (brace == -1 or bracket < brace)
    i = (bracket + 1) if in_array else brace
    objects: list[dict] = []
    saw_close = False
    incomplete = False
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if in_array and text[i] == "]":
            saw_close = True
            break
        if text[i] != "{":
            break
        blob = _balanced_from(text, i, "{", "}")
        if blob is None:
            incomplete = True
            break
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            incomplete = True
            break
        if isinstance(obj, dict):
            objects.append(obj)
        i += len(blob)
    if in_array:
        truncated = bool(objects) and (incomplete or not saw_close)
    else:
        truncated = bool(objects) and incomplete
    return objects, truncated


def _first_valid_array(text: str) -> str | None:
    """Skip the ``[`` that fail to parse (``[GOVERN]``, ``[Section A]`` inside
    thinking) and accept the first one that ``json.loads`` into an array of objects.

    An empty array ``[]`` counts too - that chunk genuinely has no clauses. A pure
    string array (``["Section A"]``) does not; that is an example inside thinking.
    """
    import json

    cursor = 0
    while True:
        start = text.find("[", cursor)
        if start == -1:
            return None
        candidate = _balanced_from(text, start)
        if candidate:
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                cursor = start + 1
                continue
            if isinstance(payload, list) and (
                not payload or any(isinstance(row, dict) for row in payload)
            ):
                return candidate
        cursor = start + 1


def _extract_json_array(raw: str) -> str | None:
    """Extract the outermost JSON array from the model's raw reply.

    In the wild the model breaks the contract in many ways:
    - prepends ``Here is the JSON:`` or appends a greeting
    - wraps everything in ```json fences
    - **interleaves supplementary remarks between segments** (minimax writes its
      thinking inside ``<think>...</think>`` first)
    - examples and lists inside the thinking contain square brackets
    - ``[`` ``]`` appear inside strings (say a clause title is ``[draft]``)
    - **the JSON is written only inside the thinking**, nothing outside the tags
      (the normal state of affairs when MiniMax-M3 splits a long document)
    - **the thinking never closes** (max_tokens ran out)

    A naive find("[")/rfind("]") mismatches in all of these scenarios - the details
    seen in the last NIST.AI.100-1 re-import (square brackets inside the <think>
    thinking) confirmed it. Balanced matching is mandatory, and the ``[`` that fail
    to parse must be skipped.

    Look at the text after stripping the thinking first; only if there is no array
    there, go back and search the thinking - an answer written on scratch paper
    still beats voiding the whole chunk.
    """
    text = _FENCE.sub("", raw or "").strip()
    spoken = _spoken(text)
    return _first_valid_array(spoken) or _first_valid_array(text)


def parse_outline(raw: str) -> tuple[list[Span], list[Problem]]:
    """Parse whatever the model replied with. **Never raises** - the caller is a
    background thread.

    Three tiers of handling, kept separate:
    - void the whole chunk: not JSON, not an array, or it wrote body text (it was
      not working to the contract)
    - drop one item: that item is missing fields (the other clauses are fine and
      must not be dragged down with it)
    - accept as-is: everything else
    """
    import json

    candidate = _extract_json_array(raw)
    payload = None
    truncated = False
    if candidate is not None:
        try:
            loaded = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            loaded = None
        if isinstance(loaded, list):
            payload = loaded
        elif loaded is not None:
            return [], [Problem("not_json", "The model reply is not an array; this chunk is dropped.")]
    if payload is None:
        salvaged, truncated = _salvage_objects(raw)
        if not salvaged:
            return [], [Problem(
                "not_json",
                "The model reply is not valid JSON, so this chunk is dropped."
                f" What it actually replied: {_raw_for_debug(raw)}")]
        payload = salvaged

    # Scan for body keys first: if it wrote body text, void the whole chunk instead
    # of dropping just that one item - it has proven it is not working to the
    # contract, so the earlier items that look normal cannot be trusted either.
    for row in payload:
        if isinstance(row, dict) and _BODY_KEYS & set(row):
            return [], [Problem(
                "has_body",
                "The model wrote body text itself, but body text may only come from your source. This chunk is dropped; you can rerun it.")]

    spans: list[Span] = []
    problems: list[Problem] = []
    if truncated:
        problems.append(Problem(
            "truncated",
            f"The model reply was truncated after clause {len(payload)}; "
            "clauses completed before the cut were kept."))
    for row in payload:
        if not isinstance(row, dict):
            problems.append(Problem("not_json", "One item in the model's array is not an object and was dropped."))
            continue
        ref = str(row.get("ref") or "").strip()
        label = str(row.get("label") or "").strip()
        parent = str(row.get("parent") or "").strip() or None
        try:
            start = int(row["from"])
            end = int(row["to"])
        except (KeyError, TypeError, ValueError):
            problems.append(Problem(
                "not_json",
                f'"{ref or label or "an unnamed clause"}" gave no line numbers and was dropped.'))
            continue
        spans.append(Span(
            ref=ref, label=label, parent=parent, start=start, end=end,
            ref_from=_source(row.get("ref_from"), ref),
            label_from=_source(row.get("label_from"), label),
        ))
    return spans, problems


def _source(value, filled: str) -> str:
    """Did the model say this number/title was copied or invented? When it does not
    say, treat "has a value" as "copied" - old replies lack this field, and it must
    not mark a number that exists in the source as AI-invented.
    """
    if str(value or "").strip() == "derived":
        return "derived"
    return "original" if filled else "derived"


def validate(spans: list[Span], total_lines: int,
             lines: list[str] | tuple = ()) -> tuple[list[Span], list[Problem]]:
    """Sort by line number, drop the out-of-range and overlapping ones, demote
    parents that point nowhere to top level.

    **Dropping and demoting are two different treatments; do not mix them.** An
    out-of-range clause has no trustworthy body and can only be dropped; a clause
    with a wrong parent has perfectly good body text, and dropping it throws away
    one of the user's policies.
    """
    problems: list[Problem] = []
    ranged: list[Span] = []
    renames: list[tuple[str, str]] = []
    # On the same start, **sort the longer span first**, otherwise a parent lands
    # after its child and the stack gets built upside down.
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if span.start < 1 or span.end > total_lines or span.start > span.end:
            problems.append(Problem(
                "out_of_range",
                f'"{_name(span)}" gave line range {span.start}-{span.end} '
                f"outside the source text ({total_lines} lines in total), dropped."))
            continue
        ranged.append(span)

    # **Clauses are a tree, not a sequence.** A span entirely inside another is
    # nesting (the a/b/c under 3.2.2), not overlap. This used to flag all of it as
    # overlap and drop it; in practice a national-standard framework PDF lost 184
    # sub-clauses that way - half the content never made it in.
    #
    # The real error is **staggered crossing** (10-20 vs 15-25): the model drew those
    # boundaries wrong and there is no salvaging them. Identical ranges are wrong
    # too: the same segment was cut twice - that is duplication, not parent and child.
    kept: list[Span] = []
    stack: list[Span] = []          # chain of currently open ancestors, outer to inner
    inferred: dict[int, str | None] = {}
    for span in ranged:
        while stack and span.start > stack[-1].end:
            stack.pop()
        if stack:
            outer = stack[-1]
            if span.end > outer.end:
                problems.append(Problem(
                    "overlap",
                    f'"{_name(span)}" gave line range {span.start}-{span.end} '
                    f'that crosses "{_name(outer)}" ({outer.start}-{outer.end}) - '
                    "neither its child nor its successor, dropped."))
                continue
            if span.start == outer.start and span.end == outer.end:
                problems.append(Problem(
                    "overlap",
                    f"'{_name(span)}' and '{_name(outer)}' cover the same lines "
                    f"({span.start}-{span.end}), the latter dropped."))
                continue
            # The parent is the innermost ancestor. If that one has no number there is nothing to attach to; leave empty.
            inferred[id(span)] = outer.ref or None
        else:
            inferred[id(span)] = None
        kept.append(span)
        stack.append(span)

    # Parents must be looked up among the **kept** spans: a dropped clause can no longer be anyone's parent.
    refs = {s.ref for s in kept if s.ref}
    fixed: list[Span] = []
    seen: set[str] = set()
    for span in kept:
        parent = span.parent
        if parent is None:
            # When the model left it empty, infer from containment - containment itself says who is whose parent.
            parent = inferred.get(id(span))
        elif parent not in refs or parent == span.ref:
            problems.append(Problem(
                "bad_parent",
                f"'{_name(span)}' names parent '{parent}', "
                "but no kept clause has that number; demoted to top level."))
            parent = inferred.get(id(span))
        # An empty number means "no number in the source", not a duplicate number - a person will fill it in on the preview page.
        ref = span.ref
        if ref and ref in seen:
            new = _disambiguate_ref(ref, parent, seen)
            renames.append((ref, new))
            ref = new
        if ref:
            seen.add(ref)
        fixed.append(Span(
            ref=ref, label=span.label, parent=parent,
            start=span.start, end=span.end,
            ref_from="derived" if ref != span.ref else span.ref_from,
            label_from=span.label_from))
    if renames:
        sample = "、".join(f"{old}→{new}" for old, new in renames[:6])
        extra = f" and {len(renames)} more" if len(renames) > 6 else ""
        problems.append(Problem(
            "catalog",
            f"Clause numbers conflicted with existing ones; auto-renamed to {sample}{extra}."
            "Appendices restarting from 1 are a common cause."))
    fixed = _trim_to_own_text(fixed, list(lines))
    return fixed, problems


def _disambiguate_ref(ref: str, parent: str | None, seen: set[str]) -> str:
    """Appendix numbering restarts from 1: hang it under the parent (D.1) instead of making someone edit six input boxes."""
    if parent:
        for cand in (f"{parent}.{ref}", f"{parent}-{ref}"):
            if cand not in seen:
                return cand
    n = 2
    while True:
        cand = f"{ref}-{n}"
        if cand not in seen:
            return cand
        n += 1


# Holes of one or two lines fold into a single sentence; three or more get named.
#
# Clause title lines do not enter the body (design §1.2), so **every clause leaves
# a one-line hole behind**. In practice a 31-line policy reported 7 uncovered spots,
# 5 of which were just title lines; a real 600-line policy would report dozens, and
# the real problem - "a whole chapter is missing" - would be invisible in the noise.
#
# Folding is not hiding: the count is still reported, it just no longer fills the
# screen one line at a time.
_HOLE_WORTH_NAMING = 3


def _trim_to_own_text(spans: list[Span], lines: list[str]) -> list[Span]:
    """A parent clause keeps only the stretch **before its first child**.

    Without trimming, the parent's body wraps the entire subtree a second time - the
    same paragraph gets fed to the drafter twice (paying twice), the same thing gets
    counted twice in the self-assessment, and one sentence appears twice in the
    exported SoA. In practice, 160 of 197 clauses in a national-standard framework
    PDF were affected.

    When a parent is just a grouping heading with children immediately below it, the
    trimmed result is empty. Empty is correct: `slice_lines` returns "" for
    start > end, and the preview page states plainly that this clause has no body of
    its own.

    **Closing sentences at the end fall out** (the lines after the parent's last
    child). They do not vanish silently - `uncovered()` reports the line numbers, and
    a person decides whether to merge them back in.
    """
    out = []
    for span in spans:
        children = [
            other for other in spans
            if other is not span
            and span.start <= other.start and other.end <= span.end
        ]
        if children:
            first = min(children, key=lambda s: s.start)
            end = first.start - 1
            # The child's **title line** does not belong to the parent either. The
            # prompt tells `from` to skip its own title, so that line lands in the
            # previous clause - in practice "Control Matrix"'s body became "GOVERN",
            # and GOVERN was exactly its child's title.
            #
            # Peel one extra line only when that line really carries the child's title:
            # peeling too little leaves one line of noise behind, peeling too much
            # throws away the user's body text. Children whose title shares the line
            # with the body are unaffected.
            if (first.label and 1 <= end <= len(lines)
                    and first.label in lines[end - 1]):
                end -= 1
            span = Span(ref=span.ref, label=span.label, parent=span.parent,
                        start=span.start, end=end)
        out.append(span)
    return out


# A 1-2 line hole is the next clause's title. 3-8 lines with no new heading are most
# likely continuation lines of the clause above that got cut short.
_MIN_CONTINUE_GAP = 3
_MAX_CONTINUE_GAP = 8

_TOC_PAGE = re.compile(r"\s+\d{1,3}$")
_SECTION_HEADING = re.compile(
    r"^(?P<summary>Executive Summary)\s*$"
    r"|^(?P<app>Appendix|附录)\s+(?P<appref>[A-Z一二三四五六七八九十])"
    r"(?:\s*[:：]\s*(?P<applabel>.*))?$"
    r"|^(?P<part>Part\s+\d+)\s*[:：]\s*(?P<partlabel>.{0,60})$"
    r"|^(?P<num>\d+(?:\.\d+)*)[、.．]\s+(?P<numlabel>[A-Z][^.]*)$",
    re.I,
)


def parse_section_heading(line: str) -> tuple[str, str] | None:
    """Section headings like Appendix, Part, Executive Summary, "6. AI RMF Profiles".

    A table-of-contents line ends with a page number ("Appendix A: ... 35"); that is
    not a body heading. An entry with a trailing period, like "6. Be useful to a wide
    range...", is not a new chapter either.
    """
    text = (line or "").strip()
    if not text or _is_toc_heading(text):
        return None
    match = _SECTION_HEADING.match(text)
    if not match:
        return None
    if match.group("summary"):
        return "Summary", "Executive Summary"
    if match.group("app"):
        ref = match.group("appref")
        label = (match.group("applabel") or "").strip(" :：")
        return ref, label
    if match.group("part"):
        ref = match.group("part").replace(" ", "")
        return ref, (match.group("partlabel") or "").strip()
    if match.group("num"):
        label = (match.group("numlabel") or "").strip()
        if len(text) > 60:
            return None
        return match.group("num"), label
    return None


def _is_toc_heading(text: str) -> bool:
    return bool(_TOC_PAGE.search(text)) and len(text) < 90


def _holes(spans: list[Span], total: int) -> list[tuple[int, int]]:
    holes: list[tuple[int, int]] = []
    cursor = 1
    for span in sorted(spans, key=lambda s: s.start):
        if span.start > cursor:
            holes.append((cursor, span.start - 1))
        cursor = max(cursor, span.end + 1)
    if cursor <= total:
        holes.append((cursor, total))
    return holes


def close_small_gaps(spans: list[Span], lines: list[str]) -> list[Span]:
    """The clause above got cut short and the next heading has not arrived yet: merge
    the continuation lines in between.

    Chunking can cut mid-sentence (NIST.AI.100-1's 3.3 stops at line 547, and it is
    13 lines before 3.4 starts). Take the lines back up to "the line before the next
    clause's heading" instead of a hard 8-line cap. A 1-line hole is still not merged
    - that is the next clause's title.
    """
    if not spans or not lines:
        return spans
    ordered = sorted(spans, key=lambda s: (s.start, -s.end))
    by_id = {id(s): s for s in ordered}
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start <= prev.end + 1:
            continue
        heading = _heading_line_of(lines, prev.end + 1, nxt.start - 1, nxt)
        if heading is not None:
            new_end = heading - 1
        else:
            gap = nxt.start - prev.end - 1
            if gap < _MIN_CONTINUE_GAP or gap > _MAX_CONTINUE_GAP:
                continue
            chunk = lines[prev.end:nxt.start - 1]
            if any(parse_section_heading(ln) for ln in chunk):
                continue
            new_end = nxt.start - 1
        if new_end > prev.end:
            by_id[id(prev)] = Span(
                ref=prev.ref, label=prev.label, parent=prev.parent,
                start=prev.start, end=new_end,
                ref_from=prev.ref_from, label_from=prev.label_from)
    return [by_id[id(s)] for s in ordered]


def _heading_line_of(lines: list[str], lo: int, hi: int, nxt: Span) -> int | None:
    """Which line of the hole is the next clause's heading. None when there is none."""
    for index in range(lo, hi + 1):
        line = lines[index - 1].strip()
        if not line:
            continue
        if nxt.label and nxt.label in line and len(line) <= 80:
            if not nxt.ref or line.startswith(str(nxt.ref)):
                return index
        if nxt.ref and re.match(
                rf"^{re.escape(str(nxt.ref))}(\s|$|[：:、.．])", line):
            rest = line[len(str(nxt.ref)):].strip(" ：:、.．")
            if rest and len(rest) <= 60 and not rest.endswith("."):
                return index
    return None


def fill_heading_holes(spans: list[Span], lines: list[str]) -> list[Span]:
    """When a large stretch goes unclaimed, cut it apart by Appendix / Part / "6. Title".

    The prompt says "a list of annexes is not clauses", and the model often skips an
    entire Appendix A chapter. Lines 1302-1620 of NIST.AI.100-1 went empty exactly
    like that.
    """
    if not lines:
        return spans
    occupied = set()
    refs = {s.ref for s in spans if s.ref}
    for span in spans:
        occupied.update(range(span.start, span.end + 1))
    extra: list[Span] = []
    for lo, hi in _holes(spans, len(lines)):
        hits: list[tuple[int, str, str]] = []
        for index in range(lo, hi + 1):
            if index in occupied:
                continue
            parsed = parse_section_heading(lines[index - 1])
            if parsed:
                hits.append((index, parsed[0], parsed[1]))
        if not hits:
            continue
        for pos, (start, ref, label) in enumerate(hits):
            if _heading_ref_taken(ref, refs):
                continue
            next_start = hits[pos + 1][0] if pos + 1 < len(hits) else hi + 1
            end = next_start - 1
            while end > start and _line_is_chrome(lines[end - 1]):
                end -= 1
            if not label and start < end:
                label = lines[start].strip().rstrip(":")
            extra.append(Span(
                ref=ref, label=label, parent=None,
                start=start, end=max(start, end),
                ref_from="original",
                label_from="original" if label else "derived",
            ))
            refs.add(ref)
    return spans + extra


def _heading_ref_taken(ref: str, refs: set[str]) -> bool:
    """The bare "D" and "Appendix D" name the same appendix; once the model claimed one form, do not add the other."""
    if ref in refs:
        return True
    if len(ref) == 1 and f"Appendix {ref}" in refs:
        return True
    if ref.startswith("Appendix "):
        letter = ref.split()[-1]
        if letter in refs:
            return True
    return False


def _line_is_chrome(line: str) -> bool:
    from framework_reader.userframework.catalog import _is_chrome
    return _is_chrome(line)


def uncovered(spans: list[Span], total_lines: int,
              lines: list[str] | tuple = ()) -> list[Problem]:
    """Which lines no clause captured. **Never dropped silently** - the user needs to
    know that content did not make it in.

    The Categories / Continued / Page N rows left behind by table pagination look
    like "missed cuts" but are really page furniture. When source lines are
    available, such holes fold into the "one or two lines" bucket instead of being
    named individually. The cover page / table of contents at the top is reported
    separately, not mixed into "could not be cut into clauses".
    """
    holes = _holes(spans, total_lines)
    big: list[tuple[int, int]] = []
    small = 0
    front: tuple[int, int] | None = None
    for lo, hi in holes:
        if _is_front_matter_hole(lo, hi, lines):
            front = (lo, hi)
            continue
        if hi == total_lines and hi - lo + 1 <= 3:
            small += 1
            continue
        if hi - lo + 1 >= _HOLE_WORTH_NAMING and not _hole_is_chrome(lo, hi, lines):
            big.append((lo, hi))
        else:
            small += 1
    out: list[Problem] = []
    if front:
        lo, hi = front
        out.append(Problem(
            "front_matter",
            f"Lines {lo}–{hi} at the top are cover page and table of contents, not clauses - that is expected."))
    out.extend(
        Problem("uncovered", f"Lines {lo}–{hi} of the source could not be cut into clauses.")
        for lo, hi in big
    )
    if small:
        out.append(Problem(
            "uncovered",
            f"{small} more spot(s) of one or two uncaptured lines - most likely clause titles "
            "or section markers, which is expected."))
    return out


def _hole_is_chrome(lo: int, hi: int, lines: list[str] | tuple) -> bool:
    if not lines or hi < lo:
        return False
    chunk = lines[lo - 1:hi]
    if not chunk:
        return False
    return all(not ln.strip() or _line_is_chrome(ln) for ln in chunk)


def _is_front_matter_hole(lo: int, hi: int, lines: list[str] | tuple) -> bool:
    if lo != 1 or not lines or hi < lo:
        return False
    text = "\n".join(lines[:hi]).lower()
    return "table of contents" in text or "目录" in text


def _name(span: Span) -> str:
    """An error must identify which clause it is about, otherwise the user cannot match it against the source."""
    return span.ref or span.label or "an unnamed clause"


def plan_calls(text: str) -> list[tuple[int, int]]:
    """How many calls to make and which lines each one covers. **Every line falls in
    exactly one range** - nothing missed, nothing doubled.

    If it fits, do it in one shot: the model sees the full text, the section
    hierarchy and numbering scheme are right in front of it, the cut is at its most
    accurate, and the cross-chunk merging problem disappears - that is the hardest
    part of this pipeline to get right. Design §2.1
    """
    lines = text.splitlines()
    if not lines:
        return []
    if sum(len(line) for line in lines) <= ONE_SHOT_MAX_CHARS:
        return [(1, len(lines))]
    out: list[tuple[int, int]] = []
    start = 1
    size = 0
    for index, line in enumerate(lines, start=1):
        # The `size and` guard exists for "a single line already exceeds the cap"
        # (a table extracted as one line): it cannot be split, so let it stand as a
        # chunk of its own. Without this check, the loop spins out a pile of empty
        # ranges here.
        if size and size + len(line) > ONE_SHOT_MAX_CHARS:
            out.append((start, index - 1))
            start, size = index, 0
        size += len(line)
    out.append((start, len(lines)))
    return out


def shift(spans: list[Span], offset: int) -> list[Span]:
    """Move chunk-local line numbers into the whole-document coordinate system.

    What the model saw as line 1 of the second chunk is line 301 of the document.
    Without this step, every clause from the second chunk onward would be cut from
    the top of the document.
    """
    if not offset:
        return spans
    return [
        Span(ref=s.ref, label=s.label, parent=s.parent,
             start=s.start + offset, end=s.end + offset)
        for s in spans
    ]


@dataclass(frozen=True)
class Outline:
    spans: list[Span]
    problems: list[Problem]
    calls: int


def load_prompt() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "prompts" / "outliner.md"
    ).read_text(encoding="utf-8")


def outline_document(text: str, *, client, model: str,
                     on_chunk=None) -> Outline:
    """Run the whole pipeline: chunk → call the model per chunk → parse → shift
    coordinates → validate → report uncovered lines.

    `client` is anything with `complete(system, messages, *, model, max_tokens)` -
    `GuardedClient` qualifies. Tests inject a fake.

    **One chunk failing must not take the other chunks down with it.** Re-running an
    entire document costs the money all over again, and the failed chunk's lines must
    be spelled out, otherwise the user never learns which part of the source was lost.
    """
    from framework_reader.llm.client import Message

    system = load_prompt()
    lines = text.splitlines()
    spans: list[Span] = []
    problems: list[Problem] = []
    calls = 0
    pieces = plan_calls(text)
    for lo, hi in pieces:
        piece = "\n".join(lines[lo - 1:hi])
        calls += 1
        try:
            raw = client.complete(
                system, [Message(role="user", content=numbered(piece))],
                model=model, max_tokens=16384)
        except Exception as exc:                  # noqa: BLE001
            # Do not report just the exception class - the cause (HTTP error,
            # response_format not supported, unexpected vendor payload ...) is all in
            # ``str(exc)``; spell the whole thing into the detail for whoever picks
            # this up next.
            problems.append(Problem(
                "not_json",
                f"Lines {lo}–{hi} did not finish;"
                f" ({type(exc).__name__}: {str(exc).splitlines()[0][:200]}), "
                "results from the other chunks were kept."))
        else:
            piece_spans, piece_problems = parse_outline(raw)
            # The model saw chunk-local line numbers; storage needs whole-document ones.
            spans.extend(shift(piece_spans, offset=lo - 1))
            problems.extend(piece_problems)
        # A failed chunk still counts as processed - otherwise the progress bar freezes right there.
        if on_chunk is not None:
            on_chunk(calls, len(pieces))
    # **Snap before validating.** When everything is off by one line, validation
    # judges a pile of otherwise-correct clauses out-of-range or overlapping - those
    # "problems" are fake; the root cause is a single offset.
    spans, snapped = snap_to_headings(spans, lines)
    problems.extend(snapped)
    # After section numbers are cut, harvest the clause table. When the model
    # swallowed the whole GOVERN 1.1 table into "5.1 Govern", this splits it apart by
    # the prefix numbering at line starts - never count on the model getting it right
    # next time. Company policies have no such prefixes, so for them this is a no-op.
    from framework_reader.userframework.catalog import apply_catalog

    spans, cataloged = apply_catalog(spans, lines)
    problems.extend(cataloged)
    # Merge back the continuation lines of a cut-short clause; re-cut appendices and
    # summaries the model skipped, from their headings.
    spans = close_small_gaps(spans, lines)
    before = len(spans)
    spans = fill_heading_holes(spans, lines)
    if len(spans) > before:
        problems.append(Problem(
            "catalog",
            f"Another {len(spans) - before} section(s)/appendix(ices) the model skipped were recovered from headings."))
    kept, validation = validate(spans, total_lines=len(lines), lines=lines)
    # Fill in numbers and titles **after validation**: drop the untrusted first, then complete the identifiers of what remains.
    kept = fill_gaps(kept, lines)
    return Outline(
        spans=kept,
        problems=problems + validation + uncovered(kept, len(lines), lines=lines),
        calls=calls,
    )


# The longest a derived title may be. Anything longer is a summary, not a title.
_LABEL_MAX = 24
_SENTENCE_END = "。！？；\n"


def fill_gaps(spans: list[Span], lines: list[str]) -> list[Span]:
    """Fill in a number or title the source does not have, and mark it `derived`.

    **This line must be drawn clearly: body text must never be AI-generated** (it is
    the user policy's own words; the drafted interpretation and the self-assessment
    evidence are all based on it), **but numbers and titles may be** - they are
    catalog identifiers, not the content of the policy. Leaving them empty means the
    clause cannot be stored at all (`user_control.id` is the primary key), which hands
    our problem to the user, and he will not fix it.

    Numbers follow the source's own scheme: parent `3.2`, filled-in children `3.2.1`,
    `3.2.2`. They look native to the source, so later mapping and report citations
    read naturally - the price is that the number alone cannot tell you which ones
    were filled in, so `ref_from` must be stored alongside and the preview page must
    flag it.

    **Never touch line numbers, never touch parents.** Filling in identifiers must
    not move boundaries.
    """
    taken = {s.ref for s in spans if s.ref}
    counters: dict[str, int] = {}
    out: list[Span] = []
    for span in spans:
        ref, ref_from = span.ref, span.ref_from
        if not ref:
            base = span.parent or ""
            # When the parent is itself a filled-in clause, `parent` is empty - fall
            # back to top-level numbering, otherwise you get things like ".1".
            ref = _next_ref(base, taken, counters)
            taken.add(ref)
            ref_from = "derived"
        elif ref_from == "original":
            ref_from = _verify(lines, span, ref)
        label, label_from = span.label, span.label_from
        if not label:
            label = _derive_label(lines, span) or ref
            label_from = "derived"
        elif label_from == "original":
            label_from = _verify(lines, span, label)
        out.append(Span(ref=ref, label=label, parent=span.parent,
                        start=span.start, end=span.end,
                        ref_from=ref_from, label_from=label_from))
    return out


# How many lines above the clause body to look - per the prompt, the number and
# title are not part of the body; they sit on the line above (or, when title and
# body share a line, on the first line itself).
_HEADING_LOOKBACK = 2


def _verify(lines: list[str], span: Span, value: str) -> str:
    """Do these characters actually exist in the source? **The model's self-report is
    exactly as untrustworthy as its output.**

    Observed in practice: it gave `ref="4"`, `label="Implementation and
    interpretation"`, both invented by itself, yet left `ref_from` unset - under "has
    a value means it came from the source", that becomes "was in the original all
    along". That is precisely the mistake this product must never make.

    A grouping heading trimmed down to no body has nothing in the source to check
    against; there, taking its word is the only option.
    """
    if span.end < span.start:
        # Grouping heading trimmed to empty: no body to check against, so take its word.
        return "original"
    lo = max(1, span.start - _HEADING_LOOKBACK)
    window = "\n".join(lines[lo - 1:span.end])
    if not window.strip():
        return "original"
    return "original" if value in window else "derived"


def _next_ref(base: str, taken: set[str], counters: dict[str, int]) -> str:
    """Under `3.2`, hand out `3.2.1`, `3.2.2` in turn, skipping numbers the source already uses."""
    while True:
        counters[base] = counters.get(base, 0) + 1
        candidate = f"{base}.{counters[base]}" if base else str(counters[base])
        if candidate not in taken:
            return candidate


def _derive_label(lines: list[str], span: Span) -> str:
    """Use the body's first sentence as the title. **This is a fallback** - normally the model comes up with a better one."""
    body = slice_lines("\n".join(lines), span.start, span.end).strip()
    if not body:
        return ""
    cut = len(body)
    for index, char in enumerate(body):
        if char in _SENTENCE_END:
            cut = index
            break
    return body[:min(cut, _LABEL_MAX)].strip("，、 　")


# How far from the body's start to search for the title line. Off by more than
# three lines is not an "off-by-one"; the cut itself is crooked and must not be
# papered over with a global shift.
_SNAP_WINDOW = 3


def snap_to_headings(spans: list[Span],
                     lines: list[str]) -> tuple[list[Span], list[Problem]]:
    """Use the title lines in the source to snap the boundaries back.

    The model's line arithmetic is **systematically off by one** - observed on the
    same document with the same prompt: one run all correct, the next run every
    clause +1. A prompt suppresses it for a while; it cannot suppress it every time.

    But code can check this itself: the model said `ref="3.1"`, and where the "3.1"
    line sits in the source is a fact; the body should start on the next line (or on
    that very line, when title and body share it). Same idea as verifying where
    numbers came from - **do not trust the model's self-report; check it against the
    source.**

    **Shift everything only when a majority of clauses agree on the same offset.**
    An individual clause that mismatches is crooked on its own; moving everyone to
    fit it would break the correct ones too.

    Report it when you move. Shift a line silently and the user never learns that we
    touched his boundaries.
    """
    if not spans:
        return spans, []
    deltas: list[int] = []
    for span in spans:
        expected = _expected_start(span, lines)
        if expected is not None:
            deltas.append(expected - span.start)
    if not deltas:
        return spans, []
    delta = max(set(deltas), key=deltas.count)
    if delta == 0 or deltas.count(delta) * 2 < len(spans):
        return spans, []
    moved = [
        Span(ref=s.ref, label=s.label, parent=s.parent,
             start=s.start + delta, end=s.end + delta,
             ref_from=s.ref_from, label_from=s.label_from)
        for s in spans
    ]
    if any(s.start < 1 or s.end > len(lines) for s in moved):
        # Out of range after shifting means the offset is bogus. Prefer not shifting - the validation layer will report out-of-range as usual.
        return spans, []
    return moved, [Problem(
        "snapped",
        f"The model's line numbers were off by {delta:+d} overall; aligned to the headings in your source. "
        "Please double-check each clause's start and end.")]


def _expected_start(span: Span, lines: list[str]) -> int | None:
    """Which line this clause's body **should** start on. None when no title line is found."""
    needle = span.ref or span.label
    if not needle:
        return None
    lo = max(1, span.start - _SNAP_WINDOW)
    hi = min(len(lines), span.start + 1)
    for number in range(lo, hi + 1):
        line = lines[number - 1]
        if needle not in line:
            continue
        # Words on the title line beyond the number and title mean the body starts on that very line.
        rest = line.replace(span.ref, "", 1).replace(span.label, "", 1)
        return number if len(rest.strip(" 　、.．：:")) > 6 else number + 1
    return None
