"""从上传的文档里取出纯文本，再切成段。见网页服务化设计 §8 S5、2026-08-25 AI 导入设计

**认四种格式：.txt / .md / .docx / .pdf。** 每多一种格式就是一条新的解析路径，
而解析别人的文件是攻击面最集中的地方——所以只加真正躲不开的那些。
中文安全团队的制度九成是 .docx，其余是 Word 导出的 .pdf。

**PDF 只收有文字层的。** 图片型扫描件一期不收：抽不出文字就当场说清楚，
而不是把一串空白喂给模型——模型会照着空白编。OCR 见 AI 导入设计 §0.1（二期）。

这个文件先前写着「.pdf 不收，排版件切出来的段落是乱的」。那个顾虑没有消失，
改由**预览确认**兜住（AI 导入设计 §5.2）：切歪了在预览里一眼看得出来，
而靠拒收挡住的代价是用户手里那份 PDF 根本进不来。
"""
import re
import zipfile

# 一段的目标长度。太短模型看不出上下文，太长会把一整章塞进 payload。
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
    """能直接给用户看的一句话。"""


def extract(filename: str, data: bytes) -> str:
    """抽出纯文本。归一化与剔除都发生在这里，不能靠调用方记得各调一次。"""
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


# PDF 里的中文常被抽成「康熙部首」或「兼容表意文字」——看着一模一样，
# 码位完全不同。实测一份 2025 年的国标框架 PDF 里有 2236 处：
# 「⼈⼯智能」是 U+2F08 + U+2F27，不是 U+4EBA + U+5DE5。
# 后果是 `fr search 人工智能` 一条都搜不到，模型收到的也是一堆怪字符。
_COMPAT_RANGES = (
    (0x2E80, 0x2EFF),   # CJK 部首补充
    (0x2F00, 0x2FDF),   # 康熙部首
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
)


# 「CJK 部首补充」区（U+2E80–U+2EFF）里的简化字部首**没有兼容分解**，
# NFKC 对它们无效——`⻛` U+2EDB 归一化之后还是 `⻛`。只能手工映射。
#
# 这张表只收**真见过的**：来自用户那份《人工智能安全治理框架 2.0》PDF。
# 不凭空补全整个区——没见过的映射写错了也不会有人发现，而错了就是
# 在用户的正文里换了一个字。碰到新的就往这儿加，加之前先确认它真出现过。
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
    """只把部首/兼容区的字换成正字。

    **不整段 NFKC。** 那会把全角「（）：；」转成半角，而全角标点在中文制度里
    是正规写法——动它就违反了「落库的正文逐字等于原文」。
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


# 目录的引导点：一长串「....」或「‥‥」。中文省略号「……」只有两个字符，
# 碰不到这个门槛；「见附件 3.2.1」里的点也是零散的。
_TOC_LEADER = re.compile(r"[.．·。]{6,}|[…]{3,}")


def strip_toc_lines(lines: list[str]) -> list[str]:
    """剔掉目录行。它们进模型的 payload 只会占 token，还可能被切成假条款。"""
    return [line for line in lines if not _TOC_LEADER.search(line)]


# 页眉页脚的判据：在**多数页**上一字不差地重复出现。阈值不是 100%——
# 首页往往是封面没有页眉，末页往往没有页脚。
_RUNNING_HEAD_RATIO = 0.6
# 少于这么多页时不判：三页里出现两次，说明不了任何事。
_RUNNING_HEAD_MIN_PAGES = 3
# 「第 12 页」「- 12 -」「12 / 88」这类每页都在变的页码，靠重复抓不到，按形状抓。
_PAGE_NUMBER = re.compile(
    r"^(第\s*\d+\s*页(\s*/?\s*共\s*\d+\s*页)?|[-—–]\s*\d+\s*[-—–]"
    r"|\d+\s*/\s*\d+|\d{1,4})$"
)


def strip_running_heads(pages: list[str]) -> list[str]:
    """剔掉页眉页脚与页码。

    **宁可留下也不误删。** 页眉落进条款正文只是噪声，人在预览页看得见；
    删掉一行真正的正文，是把用户的制度改了，而他不会知道。
    """
    if len(pages) < _RUNNING_HEAD_MIN_PAGES:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        # 按页去重再计数：同一页里重复三次的一行，不该因此被当成页眉。
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
    """每页一串。**按页返回**是为了下一步剔页眉页脚——那要靠「跨页重复」判断。

    整份都没有文字才算扫描件。混排文档里有几页是插图，不该因此整份被拒。
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
    # 中文文档最常见的两种编码都试过了，再猜下去只会得到乱码。
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
    """切成 (小标题, 正文) 的段。小标题只是给人看的定位标记。

    **按空行与标题切，不按固定字数切。** 固定字数会把一条制度要求拦腰截断，
    而接地材料最怕的就是半句话——模型会把它补完，补出来的正是幻觉。
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
