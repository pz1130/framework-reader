"""Pull plain text out of an uploaded document, then split it into chunks. See
hosted-service design §8 S5 and the 2026-08-25 AI import design.

**Four formats are recognized: .txt / .md / .docx / .pdf.** Every extra format is
another parsing path, and parsing other people's files is where the attack surface
is most concentrated - so only add the ones that truly cannot be avoided. Nine out
of ten policy documents from security teams are .docx; most of the rest are .pdf
exported from Word.

**PDFs are accepted only with a text layer.** Image-only scans are out of scope for
phase 1: if no text can be extracted, say so on the spot instead of feeding a string
of blanks to the model - the model will invent policy to match the blanks. OCR: see
AI import design §0.1 (phase 2).

This file used to say ".pdf is not accepted; paragraphs cut out of typeset files
come out a mess". That concern has not gone away; it is now covered by **preview
confirmation** (AI import design §5.2): a bad cut is obvious at a glance in the
preview, while refusing the file outright means the user's PDF never gets in at all.
"""
import re
import zipfile

# Target length of one chunk. Too short and the model lacks context; too long and a
# whole chapter lands in the payload.
CHUNK_TARGET = 700
CHUNK_MAX = 1200

SUPPORTED = (".txt", ".md", ".markdown", ".docx", ".pdf")
MAX_EXTRACTED_CHARS = 5_000_000
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 1000

_TAG = re.compile(r"<[^>]+>")
_PARA_END = re.compile(r"</w:p>")
_BREAK = re.compile(r"<w:(?:br|tab)\b[^>]*/?>")


class UnsupportedDocument(Exception):
    """A one-liner that can be shown to the user directly."""


def extract(filename: str, data: bytes) -> str:
    """Extract plain text. Normalization and stripping happen here - never rely on
    callers remembering to invoke each step themselves."""
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        raw = _from_docx(data)
    elif lower.endswith(".pdf"):
        raw = "\n".join(strip_running_heads(pdf_pages(data)))
    elif lower.endswith((".txt", ".md", ".markdown")):
        raw = _from_text(data)
    else:
        raise UnsupportedDocument(f"Only {', '.join(SUPPORTED)}.")
    if len(raw) > MAX_EXTRACTED_CHARS:
        raise UnsupportedDocument(
            f"The extracted text is over {MAX_EXTRACTED_CHARS:,} characters."
        )
    return "\n".join(strip_toc_lines(normalize_cjk(raw).splitlines()))


# Chinese extracted from PDFs often comes out as "Kangxi radicals" or "compatibility
# ideographs" - visually identical, completely different code points. A 2025
# national-standard framework PDF had 2236 occurrences in practice: the characters
# for "artificial intelligence" came out as U+2F08 + U+2F27 (radicals) instead of
# U+4EBA + U+5DE5 (ideographs).
# The consequence: a Chinese search for "artificial intelligence" matches nothing,
# and the model receives a pile of strange characters.
_COMPAT_RANGES = (
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x2F00, 0x2FDF),   # Kangxi Radicals
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
)


# The simplified radicals in the "CJK Radicals Supplement" block (U+2E80-U+2EFF)
# **have no compatibility decomposition**; NFKC does nothing for them - `⻛` U+2EDB
# is still `⻛` after normalization. Manual mapping is the only way.
#
# This table holds only mappings **actually observed**: from a user's "AI Safety
# Governance Framework 2.0" PDF. Do not fill in the whole block from thin air - a
# wrong unseen mapping would go unnoticed, and a wrong mapping silently swaps a
# character inside the user's body text. Add new ones here as they appear, after
# confirming they really occurred.
_RADICAL_TO_IDEOGRAPH = {
    "\u2ea0": "民",   # CJK RADICAL CIVILIAN
    "\u2ec5": "见",   # C-SIMPLIFIED SEE
    "\u2ed3": "长",   # C-SIMPLIFIED LONG
    "\u2ed4": "门",   # C-SIMPLIFIED GATE
    "\u2ed8": "青",   # BLUE
    "\u2edb": "风",   # C-SIMPLIFIED WIND
    "\u2eec": "齐",   # C-SIMPLIFIED EVEN
}


def normalize_cjk(text: str) -> str:
    """Swap only the radical/compatibility-block characters for their proper forms.

    **No whole-string NFKC.** That would turn full-width "（）：；" into half-width,
    and full-width punctuation is standard writing in Chinese policies - touching it
    would break "the stored body is verbatim identical to the source".
    """
    import unicodedata

    def fix(ch: str) -> str:
        code = ord(ch)
        if not any(lo <= code <= hi for lo, hi in _COMPAT_RANGES):
            return ch
        if ch in _RADICAL_TO_IDEOGRAPH:
            return _RADICAL_TO_IDEOGRAPH[ch]
        return unicodedata.normalize("NFKC", ch)

    return "".join(fix(ch) for ch in text)


# TOC leader dots: a long run of "...." or "‥‥". The Chinese ellipsis "……" is only
# two characters and never reaches this threshold; the dots in "see annex 3.2.1" are
# scattered too.
_TOC_LEADER = re.compile(r"[.．·。]{6,}|[…]{3,}")


def strip_toc_lines(lines: list[str]) -> list[str]:
    """Drop table-of-contents lines. In the model's payload they only burn tokens,
    and they can get cut into fake clauses."""
    return [line for line in lines if not _TOC_LEADER.search(line)]


# The test for running heads: repeated verbatim on **most** pages. The threshold is
# not 100% - the first page is often a cover without a header, the last often has no
# footer.
_RUNNING_HEAD_RATIO = 0.6
# Do not judge below this many pages: twice in three pages proves nothing.
_RUNNING_HEAD_MIN_PAGES = 3
# Page numbers change on every page ("Page 12" in Chinese, "- 12 -", "12 / 88"), so
# repetition cannot catch them; catch them by shape.
_PAGE_NUMBER = re.compile(
    r"^(第\s*\d+\s*页(\s*/?\s*共\s*\d+\s*页)?|[-—–]\s*\d+\s*[-—–]"
    r"|\d+\s*/\s*\d+|\d{1,4})$"
)


def strip_running_heads(pages: list[str]) -> list[str]:
    """Strip running heads, footers, and page numbers.

    **Rather keep too much than delete one real line.** A header inside a clause body
    is just noise, visible to a person on the preview page; deleting a line of
    genuine body text rewrites the user's policy without him ever knowing.
    """
    if len(pages) < _RUNNING_HEAD_MIN_PAGES:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        # Deduplicate within a page before counting: a line repeated three times on
        # the same page must not be branded a header for that.
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = len(pages) * _RUNNING_HEAD_RATIO
    repeated = {line for line, n in counts.items() if n >= threshold}
    out = []
    for page in pages:
        kept = [
            ln for ln in page.splitlines()
            if ln.strip()
            and ln.strip() not in repeated
            and not _PAGE_NUMBER.match(ln.strip())
        ]
        out.append("\n".join(kept))
    return out


def pdf_pages(data: bytes) -> list[str]:
    """One string per page. **Returning per page** is what lets the next step strip
    running heads - that detection relies on "repeated across pages".

    Only a document with no text anywhere is a scan. A mixed document with a few
    image pages must not be rejected wholesale because of them.
    """
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise UnsupportedDocument(
                f"That PDF has more than {MAX_PDF_PAGES:,} pages."
            )
        pages = []
        chars = 0
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            chars += len(text)
            if chars > MAX_EXTRACTED_CHARS:
                raise UnsupportedDocument(
                    f"The extracted text is over {MAX_EXTRACTED_CHARS:,} characters."
                )
            pages.append(text)
    except (PdfReadError, OSError, ValueError, KeyError, TypeError) as exc:
        raise UnsupportedDocument(
            "This PDF won't open - it may not be a PDF, or the file was corrupted in transfer.") from exc
    if not any(pages):
        raise UnsupportedDocument(
            "This PDF has no text layer, only scanned images. OCR is out of scope for now - "
            "upload the original Word file if you have it, or convert it to text with another tool first.")
    return pages


def _from_text(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # The two most common encodings for Chinese documents have both been tried;
    # guessing further only yields mojibake.
    raise UnsupportedDocument("Cannot detect this file's encoding. Re-save it as UTF-8 and upload again.")


def _from_docx(data: bytes) -> str:
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            member = bundle.getinfo("word/document.xml")
            if member.file_size > MAX_DOCX_XML_BYTES:
                raise UnsupportedDocument(
                    f"The Word document expands past {MAX_DOCX_XML_BYTES // (1024 * 1024)} MB."
                )
            xml = bundle.read(member).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnsupportedDocument(
            "This .docx won't open - it may be an old .doc with a renamed extension. "
            "Re-save it as .docx in Word and upload again.") from exc
    xml = _BREAK.sub(" ", xml)
    xml = _PARA_END.sub("\n", xml)
    text = _TAG.sub("", xml)
    return _unescape(text)


def _unescape(text: str) -> str:
    from html import unescape

    lines = [line.strip() for line in unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


def chunk(text: str) -> list[tuple[str, str]]:
    """Split into (subheading, body) chunks. The subheading is only a human-facing
    locator.

    **Cut on blank lines and headings, not on a fixed character count.** A fixed count
    slices a policy requirement in half, and nothing poisons grounding material like
    a half sentence - the model will complete it, and the completion is precisely a
    hallucination.
    """
    out: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            out.append((heading, body))
        buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if sum(len(b) for b in buffer) >= CHUNK_TARGET:
                flush()
            continue
        if _looks_like_heading(stripped):
            flush()
            heading = stripped
            continue
        buffer.append(stripped)
        if sum(len(b) for b in buffer) >= CHUNK_MAX:
            flush()
    flush()
    return out


_HEADING = re.compile(
    r"^(#{1,6}\s|第[一二三四五六七八九十百零〇\d]+[章节条]|"
    r"\d+(\.\d+)*[、.．\s]\s*\S{0,30}$)"
)


def _looks_like_heading(line: str) -> bool:
    if len(line) > 40:
        return False
    return bool(_HEADING.match(line))
