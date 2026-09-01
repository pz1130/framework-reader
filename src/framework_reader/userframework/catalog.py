"""从原文里收「带字母前缀的条款编号」。见 2026-08-29 NIST.AI.100-1 导入

框架 PDF 常有两套编号：章节号（5.1 Govern）和条款号（GOVERN 1.1、
PR.AA-01、AC-2、A.5.1、Article 9）。条款号经常排成表。模型跟章节号走，
会把整张表吞进一条——NIST.AI.100-1 的 5.1 一条 7223 字，里面埋了 19 条
GOVERN subcategory。

**不按框架名字特判。** 判据是形状：行首是「字母前缀 + 数字」，并且
全文里至少攒够几条，才当成一张条款表。公司制度的 5.1 / 3.2 没有字母
前缀，这里碰不到它们。

模型怎么切都可以，这一步在校验之前跑，用原文行号把表行拆开。
正文仍然按行号从原文截，不经过模型。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from framework_reader.userframework.outline import Problem, Span

# 少于此数不收。正文里偶尔引用一句「见 Article 9」不够成一张表。
MIN_HARVEST = 3

# 版式词：TABLE 1、FIGURE 5、PAGE 22 形状像条款号，但不是。
_LAYOUT = {
    "table", "figure", "fig", "page", "section", "chapter", "part",
    "appendix", "note", "notes", "box", "step", "phase", "volume",
    "version", "level", "type", "class", "group", "see", "the",
    "list", "item", "row", "column",
}

# 按「更具体的先匹配」。捕获组 1 = 编号，组 2 = 行上剩下的字。
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

# 新的一章 / 新的一张表（不是 Continued）。到这儿就不是上一条的正文了。
# 「6. AI RMF Profiles」这种多词标题也要认——`\\S{1,30}` 只吃一个词，
# 会让最后一条条款把下一章简介吞进去。
_SECTION_BREAK = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+[章节条]|"
    r"\d+(?:\.\d+)*[、.．]?\s+\S.{0,40}$|"
    r"appendix\s+[a-z]\b)",
    re.I,
)

# GOVERN 1.1 这种「单词 + 数字」容易误伤行首的「RMF 1.0」。
# 同一前缀至少两条才算一张表；AC-2 / Article 9 / A.5.1 形状更窄，单条也收。
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
    """行首是条款编号就回 (编号, 行上剩下的字)，否则 None。"""
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
    """全文扫描。每一条从自己的编号行到下一条编号之前，
    碰到新章节或表题就停，末尾的版式行（Continued on next page）剥掉。
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
            continuation = lines[start].strip()  # 下一行，0-based = start
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
    """把条款表拆进已有的切分结果。条目不够就当没看见。"""
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
    # 新表题不论长短（「Table 2: Categories and subcategories for the MAP
    # function.」超过 40 字）。Continued 是同一张表翻页，不是分界。
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
    """「RMF 1.0」这种换行残留只有一条，GOVERN 1 / 1.1 / 1.2 才是表。"""
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
