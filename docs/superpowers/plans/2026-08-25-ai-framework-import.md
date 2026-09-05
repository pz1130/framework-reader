# 从 Word / PDF 导入框架（一期）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户把 `.docx` 或有文字层的 `.pdf` 制度文件传进来，由模型划出条款边界、由代码从原文逐字截出正文，人在预览页确认后才落库。

**Architecture:** 一条新管线，与既有表格导入在 `POST /import` 按后缀分流。模型只回 `{ref,label,parent,from,to}`，**永远不回正文**——正文由 `slice_lines()` 从原文快照按行号截取。校验、分块、合并全在代码里，模型的输出一律视为不可信输入。预览态落在用户库（不是内存），因为它代表已经花掉的钱。

**Tech Stack:** Python 3.12、FastAPI、SQLite、pytest。新增依赖只有 `pypdf`。

**Spec:** `docs/superpowers/specs/2026-08-25-ai-framework-import-design.md`

## Global Constraints

- **模型永远不产出正文。** 输出里出现 `body` 键 = 格式错误，整块作废（spec §1.2）。
- **落库的正文逐字等于原文对应行。** 这是整份设计的地基，必须有独立测试（spec §8）。
- **不静默跳过。** 没能切出条款的行，预览页必须报出行号区间（spec §2.2，沿用 `importer.py` 那条规矩）。
- **不新增出网点。** 走 `llm/registry.py` → `GuardedClient`。`tests/test_no_network_in_tests.py` 的 `HTTPX_ALLOWED` 白名单**一个字都不改**——它红了说明设计走错了（spec §6）。
- **测试不许读进程环境、不许 import `httpx`/`anthropic`、不许出现 `_default_post` 等字样**（`tests/test_no_network_in_tests.py` 三条守卫）。注意：注释里提到 `httpx` 也会触发 src 侧白名单检查（纯文本 grep），措辞避开这个词。
- **一期不收扫描件。** 抽不出文字层的 PDF 当场拒绝，不做 OCR（spec §0.1）。
- **`ONE_SHOT_MAX_CHARS = 40000`**（spec §2.1）。
- **文档导入要同时有 `framework:import` 与 `interpretation:draft`**；表格导入门槛不变（spec §4.1）。
- 注释与用户可见文案一律中文，与仓库既有风格一致。CSS 注释会发给浏览器，里面不许出现 `**`（`test_the_description_is_not_raw_markdown` 会红）。

## 命名约定（跨任务一致，先钉死）

模型面（JSON）用 `from` / `to`；**Python 侧一律用 `start` / `end`**——`from` 是关键字。转换只发生在 `parse_outline()` 一处。

```python
# src/framework_reader/userframework/outline.py 顶部
@dataclass(frozen=True)
class Span:
    ref: str
    label: str
    parent: str | None
    start: int          # 1-based，含
    end: int            # 1-based，含

@dataclass(frozen=True)
class Problem:
    kind: str           # out_of_range | overlap | bad_parent | not_json | has_body | uncovered
    detail: str         # 直接渲到预览页的一句中文
```

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/framework_reader/userframework/extract.py` | 改：加 `.pdf` 文字层抽取与页眉页脚剔除 |
| `src/framework_reader/userframework/outline.py` | **新**：`Span`/`Problem`、行号快照、解析、校验、分块、合并、编排。纯函数，模型客户端注入 |
| `src/framework_reader/userframework/import_draft.py` | **新**：`import_draft` 表的读写 |
| `src/framework_reader/pack/user_schema.sql` | 改：加 `import_draft` 表 |
| `src/framework_reader/prompts/outliner.md` | **新**：提示词 |
| `src/framework_reader/web/jobs.py` | 改：加一种任务（切分） |
| `src/framework_reader/web/app.py` | 改：`/import` 分流、预览页、编辑动作、确认 |
| `src/framework_reader/web/views.py` | 改：导入框 `accept`；预览页 |
| `pyproject.toml` | 改：加 `pypdf` |

---

### Task 1: PDF 按页抽文字层，抽不出就当场拒

**Files:**
- Modify: `src/framework_reader/userframework/extract.py`
- Modify: `pyproject.toml`
- Test: `tests/userframework/test_extract_pdf.py` (create)

**Interfaces:**
- Consumes: 无
- Produces: `extract.pdf_pages(data: bytes) -> list[str]`（每页一串，页内保留换行）；`extract.SUPPORTED` 增加 `.pdf`；`extract.extract(filename, data) -> str` 支持 `.pdf`

- [x] **Step 1: 加依赖**

`pyproject.toml` 的 `dependencies` 里加一行（放在 `python-multipart` 之后）：

```toml
    # 只为 PDF 的文字层。docx 那条路仍是零依赖（zipfile + 正则）。
    "pypdf>=5.0",
```

装：`.venv/bin/pip install -e ".[dev]"`

- [x] **Step 2: 写失败的测试**

`tests/userframework/test_extract_pdf.py`：

```python
"""PDF 的文字层。见 2026-08-25 AI 导入设计 §2

**一期不收扫描件。** 抽不出文字就说清楚「这份 PDF 里没有文字，只有扫描图片」，
而不是把一串空白喂给模型——模型会照着空白编。
"""
import pytest

from framework_reader.userframework.extract import (
    UnsupportedDocument, extract, pdf_pages,
)


def _pdf(pages: list[str]) -> bytes:
    """现造一份带文字层的最小 PDF。不落测试固件，省得二进制进仓库。"""
    from pypdf import PdfWriter
    import io
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        page.extract_text = lambda t=text: t          # 只为构造，见下
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_a_pdf_with_no_text_layer_is_refused():
    """空白页 = 没有文字层。一期不 OCR，所以要说清楚而不是给一串空白。"""
    from pypdf import PdfWriter
    import io
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(UnsupportedDocument) as exc:
        extract("x.pdf", buffer.getvalue())
    assert "扫描" in str(exc.value)


def test_pdf_is_in_the_supported_list():
    from framework_reader.userframework.extract import SUPPORTED
    assert ".pdf" in SUPPORTED


def test_a_broken_pdf_says_so_instead_of_raising_a_stack_trace():
    with pytest.raises(UnsupportedDocument) as exc:
        extract("x.pdf", b"not a pdf at all")
    assert "打不开" in str(exc.value)
```

- [x] **Step 3: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_extract_pdf.py -v`
Expected: FAIL — `ImportError: cannot import name 'pdf_pages'`

- [x] **Step 4: 实现**

`extract.py` 顶部那段文件注释改成：

```python
"""从上传的文档里取出纯文本，再切成段。见网页服务化设计 §8 S5

**认四种格式：.txt / .md / .docx / .pdf。** 每多一种格式就是一条新的解析路径，
而解析别人的文件是攻击面最集中的地方——所以只加真正躲不开的那些。
中文安全团队的制度九成是 .docx，其余是 Word 导出的 .pdf。

**PDF 只收有文字层的。** 图片型扫描件一期不收：抽不出文字就当场说清楚，
而不是把一串空白喂给模型——模型会照着空白编。OCR 见
2026-08-25 AI 导入设计 §0.1（二期）。
"""
```

`SUPPORTED` 与 `extract()` 分支：

```python
SUPPORTED = (".txt", ".md", ".markdown", ".docx", ".pdf")


def extract(filename: str, data: bytes) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return _from_docx(data)
    if lower.endswith(".pdf"):
        return "\n".join(pdf_pages(data))
    if lower.endswith((".txt", ".md", ".markdown")):
        return _from_text(data)
    raise UnsupportedDocument(f"只收 {'、'.join(SUPPORTED)}。")


def pdf_pages(data: bytes) -> list[str]:
    """每页一串。**按页返回**是为了下一步剔页眉页脚——那要靠「跨页重复」判断。"""
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, OSError, ValueError) as exc:
        raise UnsupportedDocument(
            "这个 PDF 打不开——可能不是 PDF，或者文件在传输中损坏了。") from exc
    if not any(pages):
        raise UnsupportedDocument(
            "这份 PDF 里没有文字，只有扫描图片。一期不做 OCR——"
            "有原始的 Word 就传 Word，没有的话先用别的工具转成文字版。")
    return pages
```

- [x] **Step 5: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v && .venv/bin/pytest -q`
Expected: 全绿。特别确认 `tests/test_no_network_in_tests.py` 仍绿。

- [x] **Step 6: 提交**

```bash
git add pyproject.toml src/framework_reader/userframework/extract.py tests/userframework/test_extract_pdf.py
git commit -m "feat(import): PDF 文字层抽取，扫描件当场拒"
```

---

### Task 2: 剔掉页眉页脚

**Files:**
- Modify: `src/framework_reader/userframework/extract.py`
- Test: `tests/userframework/test_extract_pdf.py`

**Interfaces:**
- Consumes: `pdf_pages(data) -> list[str]`
- Produces: `extract.strip_running_heads(pages: list[str]) -> list[str]`

- [x] **Step 1: 写失败的测试**

追加到 `tests/userframework/test_extract_pdf.py`：

```python
from framework_reader.userframework.extract import strip_running_heads


def test_a_line_repeated_on_every_page_is_a_running_head():
    """页眉会落进条款正文里，而它不是条款的一部分。"""
    pages = [
        "ACME 信息安全管理办法\n五、账号管理\n第 1 页",
        "ACME 信息安全管理办法\n六、口令策略\n第 2 页",
        "ACME 信息安全管理办法\n七、日志留存\n第 3 页",
    ]
    got = strip_running_heads(pages)
    assert "ACME 信息安全管理办法" not in "\n".join(got)
    assert "五、账号管理" in got[0]


def test_page_numbers_go_too():
    """「第 1 页」每页都变，靠重复判断抓不到——按形状抓。"""
    pages = ["正文一\n第 1 页", "正文二\n第 2 页", "正文三\n第 3 页"]
    joined = "\n".join(strip_running_heads(pages))
    assert "第 1 页" not in joined
    assert "正文一" in joined


def test_a_line_that_only_repeats_twice_in_a_long_document_survives():
    """「本节要求」在两页出现过，不代表它是页眉。误删正文比留下页眉糟得多。"""
    pages = ["本节要求\nA", "别的\nB", "本节要求\nC", "别的\nD",
             "别的\nE", "别的\nF"]
    joined = "\n".join(strip_running_heads(pages))
    assert joined.count("本节要求") == 2


def test_a_two_page_document_keeps_everything():
    """两页的文档里「重复」说明不了任何事，样本太小。"""
    pages = ["标题\nA", "标题\nB"]
    joined = "\n".join(strip_running_heads(pages))
    assert joined.count("标题") == 2
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_extract_pdf.py -v`
Expected: FAIL — `cannot import name 'strip_running_heads'`

- [x] **Step 3: 实现**

`extract.py` 加：

```python
# 页眉页脚的判据：在**多数页**上一字不差地重复出现。
# 阈值定在 0.6 而不是 1.0，因为首页往往没有页眉、末页往往没有页脚。
_RUNNING_HEAD_RATIO = 0.6
# 少于这么多页时不判：三页里出现两次，说明不了任何事。
_RUNNING_HEAD_MIN_PAGES = 3
# 「第 12 页」「- 12 -」「12 / 88」这类每页都变的页码，靠重复抓不到，按形状抓。
_PAGE_NUMBER = re.compile(
    r"^(第\s*\d+\s*页(\s*/?\s*共\s*\d+\s*页)?|[-—–]\s*\d+\s*[-—–]|\d+\s*/\s*\d+|\d{1,4})$"
)


def strip_running_heads(pages: list[str]) -> list[str]:
    """剔掉页眉页脚。**宁可留下也不误删**——页眉落进正文只是噪声，
    删掉一行正文是把用户的制度改了，而他不会知道。
    """
    if len(pages) < _RUNNING_HEAD_MIN_PAGES:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = len(pages) * _RUNNING_HEAD_RATIO
    repeated = {line for line, n in counts.items() if n >= threshold}
    out = []
    for page in pages:
        kept = [
            ln for ln in page.splitlines()
            if ln.strip() and ln.strip() not in repeated
            and not _PAGE_NUMBER.match(ln.strip())
        ]
        out.append("\n".join(kept))
    return out
```

`extract()` 的 `.pdf` 分支改成 `return "\n".join(strip_running_heads(pdf_pages(data)))`。

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v && .venv/bin/pytest -q`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/extract.py tests/userframework/test_extract_pdf.py
git commit -m "feat(import): 剔掉 PDF 的页眉页脚与页码"
```

---

### Task 3: 行号快照与逐字截取（**整份设计的地基**）

**Files:**
- Create: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_lines.py` (create)

**Interfaces:**
- Consumes: 无
- Produces: `outline.Span`、`outline.Problem`（见「命名约定」）、`outline.numbered(text) -> str`、`outline.slice_lines(text, start, end) -> str`、`outline.line_count(text) -> int`

- [x] **Step 1: 写失败的测试**

`tests/userframework/test_outline_lines.py`：

```python
"""行号快照与逐字截取。见 2026-08-25 AI 导入设计 §1

**这个文件里的第一条测试是整份设计的地基。** 模型只划边界，正文由代码从
原文按行号截——所以「截出来的字逐字等于原文」这件事一旦不成立，
整个方案退化成「让模型改写你的制度」。
"""
from framework_reader.userframework.outline import (
    line_count, numbered, slice_lines,
)

DOC = """五、账号管理
公司应当为每一名员工分配唯一账号，禁止共用。
离职当日停用。
六、口令策略
口令长度不少于 12 位。"""


def test_the_text_we_store_is_the_text_from_the_document():
    """地基。这条红了不许往下走。"""
    assert slice_lines(DOC, 2, 3) == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_line_numbers_are_one_based_and_inclusive():
    assert slice_lines(DOC, 1, 1) == "五、账号管理"
    assert slice_lines(DOC, 5, 5) == "口令长度不少于 12 位。"


def test_numbering_puts_a_width_four_number_in_front():
    """模型要会数行号。宽度固定、右对齐、竖线分隔，比裸数字好认。"""
    assert numbered(DOC).splitlines()[0] == "0001| 五、账号管理"
    assert numbered(DOC).splitlines()[4] == "0005| 口令长度不少于 12 位。"


def test_numbering_does_not_change_the_text_itself():
    """加行号只是给模型看的。原文快照存的是没加过的那份。"""
    stripped = "\n".join(
        line.split("| ", 1)[1] for line in numbered(DOC).splitlines())
    assert stripped == DOC


def test_line_count_matches_what_numbering_produced():
    assert line_count(DOC) == 5
    assert len(numbered(DOC).splitlines()) == 5


def test_an_out_of_range_slice_clamps_instead_of_raising():
    """越界由校验层挡（Task 5）。真漏到这儿也不该炸——炸掉的是后台线程。"""
    assert slice_lines(DOC, 4, 99) == "六、口令策略\n口令长度不少于 12 位。"
    assert slice_lines(DOC, 0, 1) == "五、账号管理"
    assert slice_lines(DOC, 99, 120) == ""
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_lines.py -v`
Expected: FAIL — `No module named 'framework_reader.userframework.outline'`

- [x] **Step 3: 实现**

`src/framework_reader/userframework/outline.py`：

```python
"""把一份连续正文切成条款。见 2026-08-25 AI 导入设计

**模型只划边界，正文由这里从原文按行号截。** 让模型直接吐正文，它会静默
把「离职当日停用」润色成「应在员工离职时及时停用其账号」——后面起草的解读、
自评的证据全基于这段正文，而它不再是这家公司的话。设计 §1.1

模型的输出一律当作不可信输入：解析、校验、合并全在代码里。
"""
from dataclasses import dataclass

# 行号宽度。四位够到 9999 行，一份 200 页的制度约 6000 行。
_WIDTH = 4


@dataclass(frozen=True)
class Span:
    """一条条款的边界。**没有 body**——正文永远从原文截。"""

    ref: str
    label: str
    parent: str | None
    start: int          # 1-based，含
    end: int            # 1-based，含


@dataclass(frozen=True)
class Problem:
    """一条能直接渲到预览页的中文说明。"""

    kind: str           # out_of_range | overlap | bad_parent | not_json | has_body | uncovered
    detail: str


def line_count(text: str) -> int:
    return len(text.splitlines())


def numbered(text: str) -> str:
    """给模型看的那一份：每行前面钉一个行号。"""
    return "\n".join(
        f"{index:0{_WIDTH}d}| {line}"
        for index, line in enumerate(text.splitlines(), start=1)
    )


def slice_lines(text: str, start: int, end: int) -> str:
    """截第 start 到第 end 行（1-based，两端含）。**逐字，不做任何加工。**

    越界就夹紧而不抛：越界本该被校验层挡掉（`validate`），真漏到这儿
    抛异常炸掉的是后台线程，用户看到的是一个永远停在「切分中」的页面。
    """
    lines = text.splitlines()
    lo = max(1, start)
    hi = min(len(lines), end)
    if lo > hi:
        return ""
    return "\n".join(lines[lo - 1:hi])
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/test_outline_lines.py -v`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/outline.py tests/userframework/test_outline_lines.py
git commit -m "feat(import): 行号快照与逐字截取——正文永远来自原文"
```

---

### Task 4: 解析模型输出

**Files:**
- Modify: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_parse.py` (create)

**Interfaces:**
- Consumes: `Span`、`Problem`
- Produces: `outline.parse_outline(raw: str) -> tuple[list[Span], list[Problem]]`

- [x] **Step 1: 写失败的测试**

`tests/userframework/test_outline_parse.py`：

```python
"""模型输出的解析。见 2026-08-25 AI 导入设计 §1.2、§2.2

模型的输出是**不可信输入**。它会裹 markdown 代码围栏、会多说一句话、
会漏字段、会自作主张塞一个 body 进来。这里一条都不信。
"""
from framework_reader.userframework.outline import parse_outline

GOOD = '[{"ref":"5.1","label":"账号管理","parent":null,"from":13,"to":14}]'


def test_a_clean_array_parses():
    spans, problems = parse_outline(GOOD)
    assert problems == []
    assert len(spans) == 1
    assert spans[0].ref == "5.1"
    assert spans[0].label == "账号管理"
    assert spans[0].parent is None
    assert (spans[0].start, spans[0].end) == (13, 14)


def test_a_markdown_fence_is_peeled_off():
    """提示词说了「只输出 JSON」，模型照样会裹 ```json。为这个作废一整块不值。"""
    spans, problems = parse_outline("```json\n" + GOOD + "\n```")
    assert problems == []
    assert len(spans) == 1


def test_a_body_key_voids_the_whole_block():
    """正文只能来自原文。模型吐了 body 说明它没在按契约干活，
    这一块的其余部分也不能信。"""
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"x","parent":null,"from":1,"to":2,'
        '"body":"我编的正文"}]')
    assert spans == []
    assert [p.kind for p in problems] == ["has_body"]


def test_not_json_at_all_voids_the_block():
    spans, problems = parse_outline("这份文档看起来是一份会议纪要。")
    assert spans == []
    assert [p.kind for p in problems] == ["not_json"]


def test_an_object_instead_of_an_array_voids_the_block():
    spans, problems = parse_outline('{"ref":"5.1"}')
    assert spans == []
    assert [p.kind for p in problems] == ["not_json"]


def test_an_empty_array_is_not_an_error():
    """这一段确实没有条款（目录页、附录）。空不是错。"""
    spans, problems = parse_outline("[]")
    assert spans == []
    assert problems == []


def test_an_entry_missing_from_or_to_is_dropped_not_fatal():
    """一条缺字段不该带走整块。丢它，并说出丢了哪一条。"""
    spans, problems = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":1,"to":2},'
        ' {"ref":"5.2","label":"b","parent":null}]')
    assert [s.ref for s in spans] == ["5.1"]
    assert [p.kind for p in problems] == ["not_json"]
    assert "5.2" in problems[0].detail


def test_string_line_numbers_are_accepted():
    """模型常把数字写成字符串。为这个丢掉一条正确的边界不值。"""
    spans, _ = parse_outline(
        '[{"ref":"5.1","label":"a","parent":null,"from":"13","to":"14"}]')
    assert (spans[0].start, spans[0].end) == (13, 14)


def test_an_empty_parent_string_becomes_none():
    spans, _ = parse_outline(
        '[{"ref":"5.1","label":"a","parent":"","from":1,"to":2}]')
    assert spans[0].parent is None
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_parse.py -v`
Expected: FAIL — `cannot import name 'parse_outline'`

- [x] **Step 3: 实现**

`outline.py` 追加：

```python
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_outline(raw: str) -> tuple[list[Span], list[Problem]]:
    """解析模型回的那一段。**任何异常都不许逃出去**——调用方是后台线程。"""
    import json

    text = _FENCE.sub("", (raw or "").strip())
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [], [Problem("not_json", "模型没有按格式回（不是 JSON），这一段作废。")]
    if not isinstance(payload, list):
        return [], [Problem("not_json", "模型回的不是一个数组，这一段作废。")]

    spans: list[Span] = []
    problems: list[Problem] = []
    for row in payload:
        if not isinstance(row, dict):
            problems.append(Problem("not_json", "模型回的数组里有一项不是对象，已丢弃。"))
            continue
        if "body" in row:
            # 契约是「只划边界」。它吐了正文，说明它没在按契约干活——
            # 这一块的其余部分也不能信。
            return [], [Problem(
                "has_body",
                "模型自己写了正文，而正文只能来自你的原文。这一段作废，可以重跑。")]
        ref = str(row.get("ref", "")).strip()
        label = str(row.get("label", "")).strip()
        parent = str(row.get("parent") or "").strip() or None
        try:
            start = int(row.get("from"))
            end = int(row.get("to"))
        except (TypeError, ValueError):
            problems.append(Problem(
                "not_json", f"「{ref or label or '一条无名条款'}」没给出行号，已丢弃。"))
            continue
        spans.append(Span(ref=ref, label=label, parent=parent, start=start, end=end))
    return spans, problems
```

顶部 `from dataclasses import dataclass` 下面加 `import re`。

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/outline.py tests/userframework/test_outline_parse.py
git commit -m "feat(import): 解析模型输出——吐了正文就整块作废"
```

---

### Task 5: 校验边界

**Files:**
- Modify: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_validate.py` (create)

**Interfaces:**
- Consumes: `Span`、`Problem`
- Produces: `outline.validate(spans: list[Span], total_lines: int) -> tuple[list[Span], list[Problem]]`

- [x] **Step 1: 写失败的测试**

`tests/userframework/test_outline_validate.py`：

```python
"""边界校验。见 2026-08-25 AI 导入设计 §2.2

一条都不信模型。越界、重叠、指向不存在的上级——每一种都要么丢掉、
要么降级，且都要说出是哪一条。
"""
from framework_reader.userframework.outline import Span, validate


def _span(ref="5.1", label="账号管理", parent=None, start=1, end=2):
    return Span(ref=ref, label=label, parent=parent, start=start, end=end)


def test_a_clean_list_passes_untouched():
    spans = [_span(start=1, end=2), _span(ref="5.2", start=3, end=4)]
    kept, problems = validate(spans, total_lines=10)
    assert kept == spans
    assert problems == []


def test_an_end_past_the_last_line_is_dropped():
    kept, problems = validate([_span(start=1, end=99)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]
    assert "5.1" in problems[0].detail


def test_a_start_below_one_is_dropped():
    kept, problems = validate([_span(start=0, end=3)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]


def test_an_end_before_its_start_is_dropped():
    kept, problems = validate([_span(start=7, end=3)], total_lines=10)
    assert kept == []
    assert [p.kind for p in problems] == ["out_of_range"]


def test_an_overlapping_span_is_dropped_and_the_earlier_one_kept():
    """留前面那条：它的边界已经被前一条确认过，后面那条才是可疑的。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=5), _span(ref="5.2", start=4, end=8)],
        total_lines=10)
    assert [s.ref for s in kept] == ["5.1"]
    assert [p.kind for p in problems] == ["overlap"]
    assert "5.2" in problems[0].detail


def test_results_come_back_sorted_by_line_even_if_the_model_shuffled_them():
    kept, _ = validate(
        [_span(ref="5.2", start=5, end=6), _span(ref="5.1", start=1, end=2)],
        total_lines=10)
    assert [s.ref for s in kept] == ["5.1", "5.2"]


def test_a_parent_that_is_not_in_the_list_is_downgraded_not_dropped():
    """上级指错了，这条条款本身还是好的。降成顶层，并说一声。"""
    kept, problems = validate(
        [_span(ref="5.1.1", parent="4.9", start=1, end=2)], total_lines=10)
    assert len(kept) == 1
    assert kept[0].parent is None
    assert [p.kind for p in problems] == ["bad_parent"]


def test_a_parent_that_is_in_the_list_survives():
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=2),
         _span(ref="5.1.1", parent="5.1", start=3, end=4)],
        total_lines=10)
    assert kept[1].parent == "5.1"
    assert problems == []


def test_a_span_pointing_at_itself_as_parent_is_downgraded():
    kept, problems = validate(
        [_span(ref="5.1", parent="5.1", start=1, end=2)], total_lines=10)
    assert kept[0].parent is None
    assert [p.kind for p in problems] == ["bad_parent"]


def test_a_duplicate_ref_is_kept_but_flagged():
    """重号在真实制度里会出现（附录里又编了一遍 1.1）。
    落库时 importer 会拒重复，所以这里要先说出来。"""
    kept, problems = validate(
        [_span(ref="5.1", start=1, end=2), _span(ref="5.1", start=3, end=4)],
        total_lines=10)
    assert len(kept) == 2
    assert [p.kind for p in problems] == ["overlap"]
    assert "重号" in problems[0].detail
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_validate.py -v`
Expected: FAIL — `cannot import name 'validate'`

- [x] **Step 3: 实现**

`outline.py` 追加：

```python
def validate(spans: list[Span], total_lines: int) -> tuple[list[Span], list[Problem]]:
    """按行号排序，丢掉越界与重叠的，把指不到的上级降成顶层。

    **丢和降是两种不同的处理，别混。** 越界的条款没有可信的正文，只能丢；
    上级指错的条款正文是好的，丢掉它等于把用户的一条制度弄丢了。
    """
    problems: list[Problem] = []
    ranged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if span.start < 1 or span.end > total_lines or span.start > span.end:
            problems.append(Problem(
                "out_of_range",
                f"「{span.ref or span.label or '一条无名条款'}」的行号 "
                f"{span.start}–{span.end} 落在原文之外（共 {total_lines} 行），已丢弃。"))
            continue
        ranged.append(span)

    kept: list[Span] = []
    for span in ranged:
        if kept and span.start <= kept[-1].end:
            problems.append(Problem(
                "overlap",
                f"「{span.ref or span.label}」的行号 {span.start}–{span.end} "
                f"和上一条（{kept[-1].ref or kept[-1].label}，"
                f"{kept[-1].start}–{kept[-1].end}）重叠，已丢弃后面那条。"))
            continue
        kept.append(span)

    refs = {s.ref for s in kept if s.ref}
    fixed: list[Span] = []
    seen: set[str] = set()
    for span in kept:
        parent = span.parent
        if parent is not None and (parent not in refs or parent == span.ref):
            problems.append(Problem(
                "bad_parent",
                f"「{span.ref or span.label}」的上级写着「{parent}」，"
                "但原文里没有这个编号，已降成顶层。"))
            parent = None
        if span.ref and span.ref in seen:
            problems.append(Problem(
                "overlap", f"编号「{span.ref}」重号，落库时会撞——改一个再导。"))
        if span.ref:
            seen.add(span.ref)
        fixed.append(Span(ref=span.ref, label=span.label, parent=parent,
                          start=span.start, end=span.end))
    return fixed, problems
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/outline.py tests/userframework/test_outline_validate.py
git commit -m "feat(import): 边界校验——越界丢、上级错降级、重号报出来"
```

---

### Task 6: 报出没被切进条款的行

**Files:**
- Modify: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_validate.py`

**Interfaces:**
- Consumes: `Span`、`Problem`
- Produces: `outline.uncovered(spans: list[Span], total_lines: int) -> list[Problem]`

- [x] **Step 1: 写失败的测试**

追加到 `tests/userframework/test_outline_validate.py`：

```python
from framework_reader.userframework.outline import uncovered


def test_lines_nobody_claimed_are_reported_with_their_numbers():
    """`importer.py`：坏行一律报错并指出行号，绝不静默跳过——
    静默跳过的结果是用户以为全导进去了。同一条规矩。"""
    problems = uncovered([_span(start=1, end=5)], total_lines=12)
    assert [p.kind for p in problems] == ["uncovered"]
    assert "6" in problems[0].detail and "12" in problems[0].detail


def test_full_coverage_reports_nothing():
    problems = uncovered(
        [_span(ref="a", start=1, end=5), _span(ref="b", start=6, end=10)],
        total_lines=10)
    assert problems == []


def test_a_hole_in_the_middle_is_reported():
    problems = uncovered(
        [_span(ref="a", start=1, end=3), _span(ref="b", start=8, end=10)],
        total_lines=10)
    assert len(problems) == 1
    assert "4" in problems[0].detail and "7" in problems[0].detail


def test_each_hole_gets_its_own_line():
    problems = uncovered(
        [_span(ref="a", start=3, end=4), _span(ref="b", start=8, end=9)],
        total_lines=12)
    assert len(problems) == 3          # 1–2、5–7、10–12


def test_nothing_extracted_at_all_reports_the_whole_document():
    problems = uncovered([], total_lines=40)
    assert len(problems) == 1
    assert "1" in problems[0].detail and "40" in problems[0].detail
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_validate.py -v`
Expected: FAIL — `cannot import name 'uncovered'`

- [x] **Step 3: 实现**

```python
def uncovered(spans: list[Span], total_lines: int) -> list[Problem]:
    """哪些行没被任何条款收进去。**不静默丢**——用户要知道有内容没进来。"""
    holes: list[tuple[int, int]] = []
    cursor = 1
    for span in sorted(spans, key=lambda s: s.start):
        if span.start > cursor:
            holes.append((cursor, span.start - 1))
        cursor = max(cursor, span.end + 1)
    if cursor <= total_lines:
        holes.append((cursor, total_lines))
    return [
        Problem("uncovered", f"原文第 {lo}–{hi} 行没能切出条款。")
        for lo, hi in holes
    ]
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/outline.py tests/userframework/test_outline_validate.py
git commit -m "feat(import): 报出没被切进条款的行"
```

---

### Task 7: 分块与一次过的取舍

**Files:**
- Modify: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_chunks.py` (create)

**Interfaces:**
- Consumes: `Span`
- Produces: `outline.ONE_SHOT_MAX_CHARS`、`outline.plan_calls(text: str) -> list[tuple[int, int]]`（每项是 1-based 含两端的行号区间）、`outline.shift(spans: list[Span], offset: int) -> list[Span]`

- [x] **Step 1: 写失败的测试**

`tests/userframework/test_outline_chunks.py`：

```python
"""塞得下就一次过，塞不下才分块。见 2026-08-25 AI 导入设计 §2.1

**分块路径平时跑不到，所以真出事时它是没被验证过的那条。** 因此这里两条
路径同等对待，且必须覆盖「一条被切在块边界上」。
"""
from framework_reader.userframework.outline import (
    ONE_SHOT_MAX_CHARS, Span, plan_calls, shift,
)


def test_a_small_document_is_one_call():
    text = "\n".join(f"第 {n} 行" for n in range(1, 51))
    assert plan_calls(text) == [(1, 50)]


def test_a_document_over_the_threshold_is_split():
    line = "啊" * 200
    text = "\n".join([line] * 400)                # 约 8 万字符
    calls = plan_calls(text)
    assert len(calls) > 1


def test_the_pieces_cover_every_line_exactly_once():
    """漏一行就是漏一条制度，重一行就是重叠——两种都不许。"""
    line = "啊" * 200
    text = "\n".join([line] * 400)
    covered = []
    for lo, hi in plan_calls(text):
        covered.extend(range(lo, hi + 1))
    assert covered == list(range(1, 401))


def test_no_piece_exceeds_the_threshold():
    line = "啊" * 200
    text = "\n".join([line] * 400)
    lines = text.splitlines()
    for lo, hi in plan_calls(text):
        assert sum(len(x) for x in lines[lo - 1:hi]) <= ONE_SHOT_MAX_CHARS


def test_a_single_line_longer_than_the_threshold_still_gets_its_own_piece():
    """一行八万字（表格被抽成一行）。切不动它，但不能因此死循环。"""
    text = "啊" * (ONE_SHOT_MAX_CHARS * 2)
    assert plan_calls(text) == [(1, 1)]


def test_shift_moves_line_numbers_into_document_coordinates():
    """模型看到的是第二块的第 1 行，那在整份文档里是第 301 行。"""
    spans = [Span(ref="5.1", label="a", parent=None, start=1, end=3)]
    got = shift(spans, offset=300)
    assert (got[0].start, got[0].end) == (301, 303)
    assert got[0].ref == "5.1"


def test_shift_by_zero_changes_nothing():
    spans = [Span(ref="5.1", label="a", parent=None, start=1, end=3)]
    assert shift(spans, offset=0) == spans
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_chunks.py -v`
Expected: FAIL — `cannot import name 'ONE_SHOT_MAX_CHARS'`

- [x] **Step 3: 实现**

```python
# 「塞得下」的判据是一个固定的保守阈值，不是模型的真实上下文——目录接口
# 只回模型 id，不回上下文长度（模型目录设计 §1），我们无从得知。
# 估错的后果是多分一次块，不是失败，所以宁可估小。
ONE_SHOT_MAX_CHARS = 40000


def plan_calls(text: str) -> list[tuple[int, int]]:
    """要发几次、每次覆盖哪几行。**每一行恰好落在一个区间里**，不漏不重。"""
    lines = text.splitlines()
    if sum(len(line) for line in lines) <= ONE_SHOT_MAX_CHARS:
        return [(1, len(lines))] if lines else []
    out: list[tuple[int, int]] = []
    start = 1
    size = 0
    for index, line in enumerate(lines, start=1):
        # 单独一行就超了（表格被抽成一行）：它自己一块，否则这里会空转。
        if size and size + len(line) > ONE_SHOT_MAX_CHARS:
            out.append((start, index - 1))
            start, size = index, 0
        size += len(line)
    out.append((start, len(lines)))
    return out


def shift(spans: list[Span], offset: int) -> list[Span]:
    """把块内行号搬到整份文档的坐标系里。"""
    if not offset:
        return spans
    return [
        Span(ref=s.ref, label=s.label, parent=s.parent,
             start=s.start + offset, end=s.end + offset)
        for s in spans
    ]
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/outline.py tests/userframework/test_outline_chunks.py
git commit -m "feat(import): 分块与一次过的取舍，块内行号搬进全局坐标"
```

---

### Task 8: 提示词与编排

**Files:**
- Create: `src/framework_reader/prompts/outliner.md`
- Modify: `src/framework_reader/userframework/outline.py`
- Test: `tests/userframework/test_outline_run.py` (create)

**Interfaces:**
- Consumes: 前七个任务的一切
- Produces: `outline.Outline`（dataclass：`spans: list[Span]`、`problems: list[Problem]`、`calls: int`）、`outline.outline_document(text: str, *, client, model: str) -> Outline`

`client` 是任何有 `complete(system, messages, *, model, max_tokens) -> str` 的对象——`GuardedClient` 就是。测试注入假的。

- [x] **Step 1: 写提示词**

`src/framework_reader/prompts/outliner.md`：

```markdown
你在把一份公司制度的正文，切成一条一条的条款。

**你不是作者。你是抄写员。**

绝对规则：

1. **你不写正文，只划边界。** 你的输出里不许出现 `body`、`text`、`content`
   这类键。正文由程序按你给的行号从原文里逐字截取。
2. **不许润色、不许改写、不许总结。** 你连正文都不经手，自然也无从改它。
3. **编号用原文里的。** 原文写「五、账号管理」，`ref` 就是「5」；写「5.1.2」
   就是「5.1.2」。原文没编号的条款，`ref` 留空字符串，人会在确认页补。
4. **标题用原文里的。** 原文那一行是什么就是什么，不要自己起名。
5. **目录页、封面、修订记录、附件清单不是条款**，跳过它们——不用勉强
   给每一行都找一个归属。

输入的每一行前面有一个行号，形如 `0013| 正文`。

只输出一个 JSON 数组，每项五个键：

- `ref`：字符串。条款编号，原文没有就填 `""`。
- `label`：字符串。条款标题。
- `parent`：字符串或 null。上级条款的 `ref`，顶层填 null。
- `from`：整数。这条条款正文的**起始行号**（不含标题行）。
- `to`：整数。结束行号，含。

不要解释，不要前后缀文字，不要 markdown 代码围栏。
```

- [x] **Step 2: 写失败的测试**

`tests/userframework/test_outline_run.py`：

```python
"""把整条管线串起来。见 2026-08-25 AI 导入设计 §2

模型一律注入假的。真实调用只在手工验收时跑，与 `fr llm check` 同一个规矩。
"""
from framework_reader.userframework.outline import (
    ONE_SHOT_MAX_CHARS, outline_document, slice_lines,
)

DOC = """五、账号管理
公司应当为每一名员工分配唯一账号，禁止共用。
离职当日停用。
六、口令策略
口令长度不少于 12 位。"""


class _Fake:
    """假客户端。形状与 GuardedClient 一致。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, system, messages, *, model, max_tokens=4096):
        self.seen.append((system, messages[0].content, model))
        return self.replies.pop(0) if self.replies else "[]"


def test_the_body_that_comes_out_is_the_body_that_went_in():
    """**地基。** 模型只给行号，正文来自原文。"""
    client = _Fake('[{"ref":"5.1","label":"账号管理","parent":null,'
                   '"from":2,"to":3}]')
    result = outline_document(DOC, client=client, model="m")
    span = result.spans[0]
    assert slice_lines(DOC, span.start, span.end) == (
        "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_the_model_sees_line_numbers():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    _, user_text, _ = client.seen[0]
    assert "0001| 五、账号管理" in user_text


def test_the_model_is_told_it_is_a_scribe():
    client = _Fake("[]")
    outline_document(DOC, client=client, model="m")
    system, _, _ = client.seen[0]
    assert "抄写员" in system


def test_a_small_document_costs_one_call():
    client = _Fake("[]")
    assert outline_document(DOC, client=client, model="m").calls == 1
    assert len(client.seen) == 1


def test_uncovered_lines_show_up_as_problems():
    client = _Fake('[{"ref":"5.1","label":"a","parent":null,"from":2,"to":3}]')
    result = outline_document(DOC, client=client, model="m")
    kinds = [p.kind for p in result.problems]
    assert "uncovered" in kinds


def test_a_span_split_across_two_chunks_lands_in_document_coordinates():
    """分块路径的关键用例：第二块的第 1 行必须换算成全文的行号。"""
    line = "啊" * 200
    text = "\n".join([line] * 400)
    client = _Fake(
        '[{"ref":"1","label":"a","parent":null,"from":1,"to":2}]',
        '[{"ref":"2","label":"b","parent":null,"from":1,"to":2}]',
        '[{"ref":"3","label":"c","parent":null,"from":1,"to":2}]',
        '[{"ref":"4","label":"d","parent":null,"from":1,"to":2}]',
    )
    result = outline_document(text, client=client, model="m")
    starts = [s.start for s in result.spans]
    assert starts == sorted(starts)
    assert starts[0] == 1
    assert starts[1] > 2          # 第二块的第 1 行不是全文第 1 行


def test_a_model_failure_in_one_chunk_does_not_kill_the_rest():
    """一块回了垃圾，其余块的结果还要留下——重跑一整份文档要重花一次钱。"""
    line = "啊" * 200
    text = "\n".join([line] * 400)
    client = _Fake(
        "我看不懂这段文字",
        '[{"ref":"2","label":"b","parent":null,"from":1,"to":2}]',
        "[]", "[]",
    )
    result = outline_document(text, client=client, model="m")
    assert [s.ref for s in result.spans] == ["2"]
    assert any(p.kind == "not_json" for p in result.problems)


def test_calls_counts_every_request_made():
    line = "啊" * 200
    text = "\n".join([line] * 400)
    client = _Fake("[]", "[]", "[]", "[]")
    result = outline_document(text, client=client, model="m")
    assert result.calls == len(client.seen)
```

- [x] **Step 3: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_outline_run.py -v`
Expected: FAIL — `cannot import name 'outline_document'`

- [x] **Step 4: 实现**

```python
@dataclass(frozen=True)
class Outline:
    spans: list[Span]
    problems: list[Problem]
    calls: int


def load_prompt() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "prompts" / "outliner.md"
            ).read_text(encoding="utf-8")


def outline_document(text: str, *, client, model: str) -> Outline:
    """跑完整条：分块 → 逐块调模型 → 解析 → 搬坐标 → 校验 → 报未覆盖。

    **一块失败不带走其余块。** 重跑一整份文档要重花一次钱。
    """
    from framework_reader.llm.client import Message

    system = load_prompt()
    lines = text.splitlines()
    spans: list[Span] = []
    problems: list[Problem] = []
    calls = 0
    for lo, hi in plan_calls(text):
        piece = "\n".join(lines[lo - 1:hi])
        calls += 1
        try:
            raw = client.complete(
                system, [Message(role="user", content=numbered(piece))],
                model=model, max_tokens=8192)
        except Exception as exc:                  # noqa: BLE001
            problems.append(Problem(
                "not_json",
                f"原文第 {lo}–{hi} 行这一段没能跑完（{type(exc).__name__}），"
                "其余段落的结果保留。"))
            continue
        piece_spans, piece_problems = parse_outline(raw)
        spans.extend(shift(piece_spans, offset=lo - 1))
        problems.extend(piece_problems)
    kept, validation = validate(spans, total_lines=len(lines))
    return Outline(
        spans=kept,
        problems=problems + validation + uncovered(kept, len(lines)),
        calls=calls,
    )
```

- [x] **Step 5: 跑，确认绿；确认白名单没红**

Run: `.venv/bin/pytest -q`
Expected: 全绿，含 `tests/test_no_network_in_tests.py`

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/prompts/outliner.md src/framework_reader/userframework/outline.py tests/userframework/test_outline_run.py
git commit -m "feat(import): 提示词与编排——一块失败不带走其余块"
```

---

### Task 9: 预览态落盘

**Files:**
- Create: `src/framework_reader/userframework/import_draft.py`
- Modify: `src/framework_reader/pack/user_schema.sql`
- Test: `tests/userframework/test_import_draft.py` (create)

**Interfaces:**
- Consumes: `outline.Span`
- Produces: `import_draft.ImportDraft`（dataclass：`draft_id`、`framework_id`、`name`、`source_text`、`spans`、`dropped`、`problems`）、`import_draft.ImportDraftStore`（`create`、`load`、`save`、`delete`）

`dropped: set[str]` 是被取消勾选的条款的**列表下标字符串**——编号可以为空也可以重号，拿它当键会撞。

- [x] **Step 1: 加表**

`src/framework_reader/pack/user_schema.sql` 末尾追加：

```sql
-- 文档导入的预览态。见 2026-08-25 AI 导入设计 §3
--
-- 为什么落盘而不是放进程内：jobs.py 里的任务状态可以丢，因为起草的结果
-- 已经在用户库里，丢的只是「跑到第几条」。这里的结果**还没进库**——
-- 重启一次，用户已经花掉的那几十次调用就没了，且他不知道为什么。
--
-- source_text 是原文快照，必须一起存：正文靠行号从它截。
CREATE TABLE IF NOT EXISTS import_draft (
    id           TEXT PRIMARY KEY,
    framework_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    source_text  TEXT NOT NULL,
    spans        TEXT NOT NULL,      -- JSON
    dropped      TEXT NOT NULL,      -- JSON 数组，被取消勾选的下标
    problems     TEXT NOT NULL,      -- JSON
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT ''
);
```

- [x] **Step 2: 写失败的测试**

`tests/userframework/test_import_draft.py`：

```python
"""预览态。见 2026-08-25 AI 导入设计 §3

**落盘，不在内存。** 它代表已经花掉的钱——重启丢掉等于让人再花一次。
"""
from framework_reader.userframework.import_draft import ImportDraftStore
from framework_reader.userframework.outline import Problem, Span


def _store(tmp_path):
    return ImportDraftStore(tmp_path / "user.sqlite")


def _spans():
    return [Span(ref="5.1", label="账号管理", parent=None, start=2, end=3)]


def test_a_draft_survives_a_new_store_object(tmp_path):
    """新建一个 store 就是「重启」——进程内状态在这一步会全丢。"""
    draft_id = _store(tmp_path).create(
        framework_id="ACME-1", name="ACME 制度", source_text="a\nb\nc",
        spans=_spans(), problems=[], actor="ann@acme.cn")
    again = _store(tmp_path).load(draft_id)
    assert again is not None
    assert again.framework_id == "ACME-1"
    assert again.spans[0].ref == "5.1"


def test_the_source_snapshot_comes_back_byte_for_byte(tmp_path):
    """正文靠行号从它截。它变了，正文就跟着变了。"""
    text = "五、账号管理\n公司应当为每一名员工分配唯一账号。\n离职当日停用。"
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text=text,
                            spans=_spans(), problems=[], actor="x")
    assert store.load(draft_id).source_text == text


def test_problems_come_back_too(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(
        framework_id="A", name="n", source_text="a", spans=[],
        problems=[Problem("uncovered", "原文第 1–9 行没能切出条款。")], actor="x")
    got = store.load(draft_id)
    assert got.problems[0].kind == "uncovered"
    assert "1–9" in got.problems[0].detail


def test_two_drafts_do_not_collide(tmp_path):
    store = _store(tmp_path)
    a = store.create(framework_id="A", name="a", source_text="x",
                     spans=[], problems=[], actor="x")
    b = store.create(framework_id="B", name="b", source_text="y",
                     spans=[], problems=[], actor="x")
    assert a != b
    assert store.load(a).framework_id == "A"
    assert store.load(b).framework_id == "B"


def test_edits_are_saved(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a\nb\nc",
                            spans=_spans(), problems=[], actor="x")
    store.save(draft_id,
               spans=[Span(ref="9.9", label="改过的", parent=None, start=1, end=1)],
               dropped={"0"})
    got = store.load(draft_id)
    assert got.spans[0].ref == "9.9"
    assert got.dropped == {"0"}


def test_a_deleted_draft_is_gone(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a",
                            spans=[], problems=[], actor="x")
    store.delete(draft_id)
    assert store.load(draft_id) is None


def test_loading_something_that_never_existed_is_none_not_a_crash(tmp_path):
    assert _store(tmp_path).load("no-such-draft") is None
```

- [x] **Step 3: 跑，确认它红**

Run: `.venv/bin/pytest tests/userframework/test_import_draft.py -v`
Expected: FAIL — `No module named '...import_draft'`

- [x] **Step 4: 实现**

`src/framework_reader/userframework/import_draft.py`：

```python
"""文档导入的预览态。见 2026-08-25 AI 导入设计 §3

**落盘，不在内存。** `web/jobs.py` 的任务状态可以丢，因为起草的结果已经在
用户库里；这里的结果还没进库，丢了就是白花的钱。
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.userframework.outline import Problem, Span
from framework_reader.userframework.store import connect


@dataclass
class ImportDraft:
    draft_id: str
    framework_id: str
    name: str
    source_text: str
    spans: list[Span]
    problems: list[Problem]
    dropped: set[str] = field(default_factory=set)


class ImportDraftStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None

    def _conn(self):
        conn = connect(self.path)
        assert conn is not None
        return conn

    def create(self, *, framework_id: str, name: str, source_text: str,
               spans: list[Span], problems: list[Problem], actor: str) -> str:
        draft_id = uuid.uuid4().hex
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO import_draft (id, framework_id, name, source_text,"
                " spans, dropped, problems, created_at, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, framework_id, name, source_text,
                 _dump_spans(spans), "[]", _dump_problems(problems),
                 datetime.now(timezone.utc).isoformat(), actor))
            conn.commit()
        finally:
            conn.close()
        return draft_id

    def load(self, draft_id: str) -> ImportDraft | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM import_draft WHERE id = ?", (draft_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ImportDraft(
            draft_id=row["id"], framework_id=row["framework_id"],
            name=row["name"], source_text=row["source_text"],
            spans=_load_spans(row["spans"]),
            problems=_load_problems(row["problems"]),
            dropped=set(json.loads(row["dropped"])),
        )

    def save(self, draft_id: str, *, spans: list[Span], dropped: set[str]) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE import_draft SET spans = ?, dropped = ? WHERE id = ?",
                (_dump_spans(spans), json.dumps(sorted(dropped)), draft_id))
            conn.commit()
        finally:
            conn.close()

    def delete(self, draft_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM import_draft WHERE id = ?", (draft_id,))
            conn.commit()
        finally:
            conn.close()


def _dump_spans(spans: list[Span]) -> str:
    return json.dumps([
        {"ref": s.ref, "label": s.label, "parent": s.parent,
         "start": s.start, "end": s.end} for s in spans], ensure_ascii=False)


def _load_spans(raw: str) -> list[Span]:
    return [Span(**row) for row in json.loads(raw)]


def _dump_problems(problems: list[Problem]) -> str:
    return json.dumps([{"kind": p.kind, "detail": p.detail} for p in problems],
                      ensure_ascii=False)


def _load_problems(raw: str) -> list[Problem]:
    return [Problem(**row) for row in json.loads(raw)]
```

- [x] **Step 5: 跑，确认绿**

Run: `.venv/bin/pytest tests/userframework/ -v && .venv/bin/pytest -q`

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/pack/user_schema.sql src/framework_reader/userframework/import_draft.py tests/userframework/test_import_draft.py
git commit -m "feat(import): 预览态落盘——它代表已经花掉的钱"
```

---

### Task 10: 路由分流、预检、异步切分

**Files:**
- Modify: `src/framework_reader/web/jobs.py`
- Modify: `src/framework_reader/web/app.py:767-820`（`import_framework`）
- Test: `tests/web/test_import_document.py` (create)

**Interfaces:**
- Consumes: `outline.outline_document`、`import_draft.ImportDraftStore`、`ModelConfig.charge_draft`
- Produces: `jobs.start_outline(key, runner) -> Job`、`jobs.get_outline(key) -> Job | None`；路由 `POST /import` 对 `.docx`/`.pdf` 返回 303 到 `/import/{draft_id}`

- [x] **Step 1: 写失败的测试**

`tests/web/test_import_document.py`：

```python
"""文档导入。见 2026-08-25 AI 导入设计 §4、§5

模型注入假的。**预检不过时一个请求都不发**——跑到一半没钱了，那半份预览
是垃圾，钱也白花。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from io import BytesIO

from framework_reader import crypto
from framework_reader.identity.store import IdentityStore
from framework_reader.llm.config import ModelConfig
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier
from framework_reader.userframework.outline import Outline, Problem, Span

DOC = ("五、账号管理\n公司应当为每一名员工分配唯一账号，禁止共用。\n"
       "离职当日停用。\n六、口令策略\n口令长度不少于 12 位。")


def _docx(text: str) -> bytes:
    """最小 .docx：一个 zip，里面一份 word/document.xml。"""
    import io
    import zipfile

    paragraphs = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("word/document.xml", f"<w:document><w:body>{paragraphs}"
                                             "</w:body></w:document>")
    return buffer.getvalue()


def _make(tmp_path, monkeypatch, outline_runner=None):
    """工厂。默认的 runner 切出一条；要别的形状就传一个进来。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann-ann",
                            roles=("author",))
    calls = {"n": 0}

    def runner(text, *, client, model):
        calls["n"] += 1
        return Outline(
            spans=[Span(ref="5.1", label="账号管理", parent=None, start=2, end=3)],
            problems=[Problem("uncovered", "原文第 1–1 行没能切出条款。")],
            calls=1)

    app = create_app(db, outline_runner=outline_runner or runner)
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"email": "ann@acme.cn",
                                "password": "pw-ann-ann-ann"})
    from framework_reader.query.api import QueryAPI
    env = type("E", (), {})()
    env.client, env.calls, env.config = client, calls, ModelConfig()
    env.reader = QueryAPI(db, user_db=None)
    return env


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _post(env, path, **data):
    page = env.client.get("/").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return env.client.post(
        path, data={"csrf": found.group(1) if found else "", **data})


def _upload(env, data: bytes, name="f.docx"):
    page = env.client.get("/").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return env.client.post(
        "/import",
        data={"framework_id": "ACME-1", "name": "ACME 制度",
              "csrf": found.group(1) if found else ""},
        files={"file": (name, BytesIO(data), "application/octet-stream")})


def test_a_docx_lands_on_a_preview_not_in_the_library(env):
    """确认前不写库。这是整个预览环节存在的理由。"""
    from framework_reader.userframework.store import UserFrameworkStore

    result = _upload(env, _docx(DOC))
    assert result.status_code == 303
    assert "/import/" in result.headers["location"]
    assert UserFrameworkStore().list_frameworks() == []


def test_a_csv_still_goes_straight_into_the_library(env):
    """表格那条路一个字都不该变。"""
    from framework_reader.userframework.store import UserFrameworkStore

    result = _upload(env, "编号,标题\n1.1,账号管理\n".encode(), name="f.csv")
    assert result.status_code == 303
    assert result.headers["location"].endswith("/f/ACME-1")
    assert [f.id for f in UserFrameworkStore().list_frameworks()] == ["ACME-1"]


def test_a_pdf_with_no_text_layer_says_so(env):
    result = _upload(env, b"not a pdf", name="f.pdf")
    assert "打不开" in result.text


def test_the_month_cap_is_checked_before_any_request_goes_out(env):
    """闸在前面。跑到一半没钱了，那半份预览是垃圾，钱也白花。"""
    env.config.set_limits(draft_cap_month=0, by="ann@acme.cn")
    result = _upload(env, _docx(DOC))
    assert env.calls["n"] == 0
    assert "上限" in result.text


def test_a_successful_import_is_charged(env):
    _upload(env, _docx(DOC))
    assert env.config.spent_this_month() >= 1


def test_a_probe_is_written_to_the_audit_log(env):
    from framework_reader.identity.store import IdentityStore

    _upload(env, _docx(DOC))
    events = [e["event"] for e in IdentityStore().audit(20)]
    assert "framework.outline" in events
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/web/test_import_document.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'outline_runner'`

- [x] **Step 3: 实现**

`create_app` 签名加 `outline_runner=None`（与既有 `draft_runner` 同一个模式）。
`import_framework` 里，写完临时文件之后按后缀分流：

```python
        DOCUMENT_SUFFIXES = (".docx", ".pdf", ".txt", ".md", ".markdown")
        if suffix.lower() in DOCUMENT_SUFFIXES:
            return _outline_upload(framework_id.strip(), name.strip(),
                                   file.filename or "", tmp.read_bytes(), fail)
```

新增私有函数（放在 `import_framework` 之前）：

```python
    def _outline_upload(framework_id: str, name: str, filename: str,
                        data: bytes, fail):
        """文档导入：抽文本 → 预检 → 切分 → 落预览态。**确认前不写框架库。**"""
        from framework_reader.llm.config import BudgetError
        from framework_reader.llm.guard import PayloadGuard
        from framework_reader.userframework import outline as outline_mod
        from framework_reader.userframework.extract import (
            UnsupportedDocument, extract,
        )
        from framework_reader.userframework.import_draft import ImportDraftStore

        # 文档导入花组织的钱，所以门槛比表格导入高一档。设计 §4.1
        if not views.may(perm.INTERPRETATION_DRAFT):
            return fail("从 Word / PDF 导入要调用模型，花的是组织的钱——"
                        "这一步需要 author 角色。表格（.csv / .xlsx）导入不受影响。")
        try:
            text = extract(filename, data)
        except UnsupportedDocument as exc:
            return fail(f"导入失败：{exc}")
        if not text.strip():
            return fail("这份文档里没有文字。")

        # 预检：先算要几次调用，闸不过就一个请求都不发。设计 §4
        planned = len(outline_mod.plan_calls(text))
        try:
            models_config.charge_draft(
                _who(None), planned, what=f"切分 {framework_id}",
                running_jobs=jobs.running_count())
        except BudgetError as exc:
            return fail(str(exc))

        registry, key_lookup = effective_registry(config=models_config)
        client = registry.build("extractor", guard=PayloadGuard([]),
                                key_lookup=key_lookup)
        run = outline_runner or outline_mod.outline_document
        result = run(text, client=client,
                     model=registry.role("extractor").model)
        identity.log("framework.outline", actor=_who(None),
                     detail=f"{framework_id} ← {filename}，"
                            f"{planned} 次调用，切出 {len(result.spans)} 条")
        draft_id = ImportDraftStore(_user_db()).create(
            framework_id=framework_id, name=name, source_text=text,
            spans=result.spans, problems=result.problems, actor=_who(None))
        return RedirectResponse(f"/import/{draft_id}", status_code=303)
```

**注意**：`PayloadGuard([])` 是空守卫——payload 是用户自己的制度正文，不是
Tier C/D 原文。这与 `fr llm check` 的探针同一个用法。

`_who(None)` 若签名不接受 None，改为把 `request: Request` 一路传进来。

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest tests/web/test_import_document.py -v && .venv/bin/pytest -q`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/web/app.py tests/web/test_import_document.py
git commit -m "feat(import): 文档走切分管线，预检不过一个请求都不发"
```

---

### Task 11: 预览页

**Files:**
- Modify: `src/framework_reader/web/views.py`
- Modify: `src/framework_reader/web/app.py`
- Test: `tests/web/test_import_document.py`

**Interfaces:**
- Consumes: `ImportDraftStore.load`、`outline.slice_lines`
- Produces: `views.import_preview(draft, bodies: list[str], nav: str) -> str`；路由 `GET /import/{draft_id}`

- [x] **Step 1: 写失败的测试**

追加到 `tests/web/test_import_document.py`：

```python
def _preview(env):
    location = _upload(env, _docx(DOC)).headers["location"]
    return env.client.get(location).text


def test_the_preview_shows_the_body_cut_from_the_document(env):
    """**地基在网页上的样子**：显示的正文就是原文第 2–3 行。"""
    page = _preview(env)
    assert "公司应当为每一名员工分配唯一账号，禁止共用。" in page


def test_the_preview_says_how_many_it_cut(env):
    assert "切出 1 条" in _preview(env)


def test_the_preview_reports_the_lines_nobody_claimed(env):
    assert "原文第 1–1 行没能切出条款" in _preview(env)


def test_the_preview_says_nothing_is_written_yet(env):
    assert "确认前不写库" in _preview(env)


def test_the_body_is_not_editable(env):
    """正文只读。能在这儿改就等于把「改写原文」从前门放进来。设计 §5.2"""
    page = _preview(env)
    assert 'name="body"' not in page
    assert "<textarea" not in page


def test_an_entry_with_no_label_starts_unchecked(tmp_path, monkeypatch):
    """空标题多半是切歪了，但也可能是真条款——不替人决定，只是不默认勾上。"""
    from framework_reader.userframework.outline import Outline, Span

    def runner(text, *, client, model):
        return Outline(
            spans=[Span(ref="5.1", label="", parent=None, start=2, end=3)],
            problems=[], calls=1)

    env = _make(tmp_path, monkeypatch, runner)
    location = _upload(env, _docx(DOC)).headers["location"]
    page = env.client.get(location).text
    assert 'value="0" checked' not in page
    assert 'name="keep" value="0"' in page      # 框还在，只是没勾上


def test_an_entry_with_a_label_starts_checked(env):
    page = _preview(env)
    assert 'value="0" checked' in page


def test_an_unknown_draft_is_404(env):
    assert env.client.get("/import/no-such-draft").status_code == 404
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/web/test_import_document.py -v`
Expected: FAIL — `GET /import/{id}` 返回 404 或 405

- [x] **Step 3: 实现路由**

```python
    @app.get("/import/{draft_id}", response_class=HTMLResponse)
    @needs(perm.FRAMEWORK_IMPORT)
    def import_preview(draft_id: str):
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.userframework.outline import slice_lines

        draft = ImportDraftStore(_user_db()).load(draft_id)
        if draft is None:
            return HTMLResponse(views.page(
                "找不到", "<p>没有这份导入草稿——可能已经确认过或放弃了。</p>",
                nav=_nav()), 404)
        bodies = [slice_lines(draft.source_text, s.start, s.end)
                  for s in draft.spans]
        return HTMLResponse(views.import_preview(draft, bodies, nav=_nav()))
```

- [x] **Step 4: 实现视图**

`views.py` 加：

```python
def import_preview(draft, bodies: list[str], nav: str = "") -> str:
    """确认前不写库。见 2026-08-25 AI 导入设计 §5.2

    **正文只读。** 它是原文——能在这儿改就等于把「模型不许改写正文」
    这条保证从前门放进来。切歪的主要形式是多切了一刀，
    「与上一条合并」把两段行号接起来就按回去了。
    """
    rows = []
    for index, (span, body) in enumerate(zip(draft.spans, bodies)):
        key = str(index)
        checked = "" if key in draft.dropped or not span.label else " checked"
        merge = ("" if index == 0 else
                 f'<button class="linky" type="submit" name="merge" '
                 f'value="{index}">↑合并</button>')
        rows.append(
            f'<div class="prow">'
            f'<label class="pick"><input type="checkbox" name="keep" '
            f'value="{key}"{checked}> 导入这条</label>'
            f'<input type="text" name="ref-{key}" value="{escape(span.ref)}" '
            'placeholder="编号">'
            f'<input type="text" name="label-{key}" value="{escape(span.label)}" '
            'placeholder="标题">'
            f'{merge}'
            f'<p class="pbody">{escape(body)}</p>'
            f'<p class="hint">原文 {span.start}–{span.end} 行</p>'
            "</div>")
    warns = "".join(
        f'<p class="warn">⚠ {escape(p.detail)}</p>' for p in draft.problems)
    body = (
        f"<style>{_IMPORT_CSS}</style>"
        f"<h2>{escape(draft.name)}</h2>"
        f'<p class="note">切出 {len(draft.spans)} 条。<strong>确认前不写库。</strong>'
        "正文是从你的原文逐字截的，不能在这儿改——要改改原文再传一次。</p>"
        + warns
        + f'<form method="post" action="/import/{escape(draft.draft_id)}/confirm">'
        + "".join(rows)
        + '<p style="margin:1.4rem 0 0">'
        f'<button type="submit" name="confirm" value="1">导入勾上的条款</button> '
        f'<a href="/import/{escape(draft.draft_id)}/discard" '
        'style="margin-left:.8rem">放弃这次导入</a></p></form>'
    )
    return page(f"{draft.name} · 确认导入", body, crumb="导入", nav=nav)


_IMPORT_CSS = """
.prow{background:var(--surface);border:1px solid var(--rule);
  padding:.9rem 1rem;margin:0 0 .7rem}
.prow input[type=text]{width:auto;display:inline-block;margin-right:.5rem}
.prow .pick{display:inline-block;margin-right:.8rem;font-size:.85rem}
.pbody{white-space:pre-wrap;margin:.6rem 0 .2rem;color:var(--body)}
.warn{color:var(--ask);font-size:.9rem;margin:.3rem 0}
"""
```

- [x] **Step 5: 跑，确认绿**

Run: `.venv/bin/pytest tests/web/test_import_document.py -v && .venv/bin/pytest -q`
特别确认 `test_the_description_is_not_raw_markdown` 仍绿（CSS 注释里不许有 `**`）。

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/web/app.py src/framework_reader/web/views.py tests/web/test_import_document.py
git commit -m "feat(import): 预览页——正文只读，未覆盖的行明写出来"
```

---

### Task 12: 合并、放弃、确认落库

**Files:**
- Modify: `src/framework_reader/web/app.py`
- Test: `tests/web/test_import_document.py`

**Interfaces:**
- Consumes: `ImportDraftStore`、`UserFrameworkStore.add_framework`
- Produces: `POST /import/{draft_id}/confirm`（`merge` 与 `confirm` 两种提交）、`GET /import/{draft_id}/discard`

- [x] **Step 1: 写失败的测试**

```python
def test_confirming_writes_the_checked_ones(env):
    from framework_reader.userframework.store import UserFrameworkStore

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm",
          confirm="1", keep="0", **{"ref-0": "5.1", "label-0": "账号管理"})
    frameworks = UserFrameworkStore().list_frameworks()
    assert [f.id for f in frameworks] == ["ACME-1"]


def test_the_body_in_the_library_is_the_body_from_the_document(env):
    """**地基，端到端。** 这条红了整份设计就白做了。"""
    from framework_reader.query.api import QueryAPI

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm",
          confirm="1", keep="0", **{"ref-0": "5.1", "label-0": "账号管理"})
    body = env.reader.control_body("ACME-1:5.1")
    assert body == ("公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。")


def test_an_unchecked_entry_is_not_written(env):
    from framework_reader.userframework.store import UserFrameworkStore

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm", confirm="1",
          **{"ref-0": "5.1", "label-0": "账号管理"})     # 没有 keep
    assert UserFrameworkStore().control_ids("ACME-1") == set()


def test_an_edited_ref_and_label_are_what_land(env):
    from framework_reader.userframework.store import UserFrameworkStore

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm", confirm="1", keep="0",
          **{"ref-0": "9.9", "label-0": "我改过的标题"})
    assert UserFrameworkStore().control_ids("ACME-1") == {"ACME-1:9.9"}


def test_the_draft_is_gone_after_confirming(env):
    from framework_reader.userframework.import_draft import ImportDraftStore

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm", confirm="1", keep="0",
          **{"ref-0": "5.1", "label-0": "账号管理"})
    assert ImportDraftStore().load(draft_id) is None


def test_confirming_with_nothing_checked_is_refused(env):
    """一条都不勾就落库，会种下一个空框架。"""
    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    page = _post(env, f"/import/{draft_id}/confirm", confirm="1",
                 **{"ref-0": "", "label-0": ""}).text
    assert "至少勾一条" in page


def test_discarding_removes_the_draft(env):
    from framework_reader.userframework.import_draft import ImportDraftStore

    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    env.client.get(f"/import/{draft_id}/discard")
    assert ImportDraftStore().load(draft_id) is None
```

合并的测试需要两条 span，用一个专门的 runner：

```python
def test_merging_joins_the_two_line_ranges(tmp_path, monkeypatch):
    """多切了一刀是切歪的主要形式。合并把两段行号接起来，正文自动重算。"""
    from framework_reader.userframework.import_draft import ImportDraftStore
    from framework_reader.userframework.outline import Outline, Span

    def runner(text, *, client, model):
        return Outline(spans=[
            Span(ref="5.1", label="账号管理", parent=None, start=2, end=2),
            Span(ref="5.2", label="被多切的一刀", parent=None, start=3, end=3),
        ], problems=[], calls=1)

    env = _make(tmp_path, monkeypatch, runner)
    location = _upload(env, _docx(DOC)).headers["location"]
    draft_id = location.rsplit("/", 1)[1]
    _post(env, f"/import/{draft_id}/confirm", merge="1",
          **{"ref-0": "5.1", "label-0": "账号管理",
             "ref-1": "5.2", "label-1": "被多切的一刀"})
    draft = ImportDraftStore().load(draft_id)
    assert len(draft.spans) == 1
    assert (draft.spans[0].start, draft.spans[0].end) == (2, 3)
    assert draft.spans[0].ref == "5.1"        # 取上一条的
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/web/test_import_document.py -v`

- [x] **Step 3: 实现**

```python
    @app.post("/import/{draft_id}/confirm")
    @needs(perm.FRAMEWORK_IMPORT)
    async def import_confirm(draft_id: str, request: Request):
        from framework_reader.userframework.import_draft import ImportDraftStore
        from framework_reader.userframework.outline import Span, slice_lines
        from framework_reader.userframework.store import UserFrameworkStore

        store = ImportDraftStore(_user_db())
        draft = store.load(draft_id)
        if draft is None:
            return HTMLResponse(views.page(
                "找不到", "<p>没有这份导入草稿。</p>", nav=_nav()), 404)

        form = await request.form()
        # 每次提交都把框里的编号与标题写回草稿——合并和确认都要它们。
        edited = [
            Span(ref=str(form.get(f"ref-{i}", s.ref)).strip(),
                 label=str(form.get(f"label-{i}", s.label)).strip(),
                 parent=s.parent, start=s.start, end=s.end)
            for i, s in enumerate(draft.spans)
        ]
        kept_keys = set(form.getlist("keep"))

        if form.get("merge"):
            index = int(form["merge"])
            # 与紧邻的上一条合并：行号取并集（中间未覆盖的行一并并进来，
            # 那正是想要的），编号与标题取上一条的。设计 §5.2
            above = edited[index - 1]
            here = edited[index]
            merged = Span(ref=above.ref, label=above.label, parent=above.parent,
                          start=min(above.start, here.start),
                          end=max(above.end, here.end))
            edited = edited[:index - 1] + [merged] + edited[index + 1:]
            store.save(draft_id, spans=edited, dropped=set())
            return RedirectResponse(f"/import/{draft_id}", status_code=303)

        chosen = [(i, s) for i, s in enumerate(edited) if str(i) in kept_keys]
        if not chosen:
            store.save(draft_id, spans=edited,
                       dropped={str(i) for i in range(len(edited))})
            return HTMLResponse(views.page(
                "还没勾", "<h2>至少勾一条</h2>"
                '<p class="note">一条都不勾就确认，会种下一个空框架。</p>'
                f'<p><a href="/import/{draft_id}">回去挑</a></p>', nav=_nav()), 400)

        controls = [
            (s.ref, s.label, s.parent,
             slice_lines(draft.source_text, s.start, s.end))
            for _, s in chosen
        ]
        UserFrameworkStore(_user_db()).add_framework(
            framework_id=draft.framework_id, name=draft.name,
            controls=controls, source_file="")
        identity.log("framework.import", actor=_who(request),
                     detail=f"{draft.framework_id}，{len(controls)} 条（文档切分）")
        store.delete(draft_id)
        return RedirectResponse(f"/f/{draft.framework_id}", status_code=303)

    @app.get("/import/{draft_id}/discard")
    @needs(perm.FRAMEWORK_IMPORT)
    def import_discard(draft_id: str):
        from framework_reader.userframework.import_draft import ImportDraftStore

        ImportDraftStore(_user_db()).delete(draft_id)
        return RedirectResponse("/import", status_code=303)
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest -q`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/web/app.py tests/web/test_import_document.py
git commit -m "feat(import): 合并、放弃、确认落库——正文逐字来自原文"
```

---

### Task 13: 导入框接受 Word 与 PDF

**Files:**
- Modify: `src/framework_reader/web/views.py:236-250`（`_import_form`）
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: 无
- Produces: 无（纯界面）

- [x] **Step 1: 写失败的测试**

追加到 `tests/web/test_app.py`：

```python
def test_the_import_box_accepts_word_and_pdf(client):
    """`accept=".csv,.xlsx,.xlsm"` 是「导入按钮无法使用」的真正成因：
    文件选择器把用户手里的 Word / PDF 全灰掉，选不中文件。"""
    page = client.get("/import").text
    assert ".docx" in page and ".pdf" in page


def test_the_import_box_says_what_happens_to_a_document(client):
    """表格直接落库、文档要先切分再确认——两条路的结果不一样，得说。"""
    page = client.get("/import").text
    assert "确认" in page
```

- [x] **Step 2: 跑，确认它红**

Run: `.venv/bin/pytest tests/web/test_app.py -k import_box -v`

- [x] **Step 3: 实现**

`views.py` 的 `_import_form` 里：

```python
        '<label for="file">文件（.csv / .xlsx / .docx / .pdf）</label>'
        '<input type="file" id="file" name="file" '
        'accept=".csv,.xlsx,.xlsm,.docx,.pdf" required>'
        '<p class="hint"><strong>表格</strong>（.csv / .xlsx）需要「编号」「标题」'
        "两列，直接入库。<br>"
        "<strong>文档</strong>（.docx / .pdf）会先让模型切成一条条条款，"
        "<strong>切完给你确认，确认前不写库</strong>——正文是从你的原文逐字截的，"
        "模型只负责划边界。<br>"
        "扫描件（图片型 PDF）暂时不收。文件只写进你们自己的库。</p>"
```

- [x] **Step 4: 跑，确认绿**

Run: `.venv/bin/pytest -q`

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/web/views.py tests/web/test_app.py
git commit -m "fix(import): 导入框收 Word 与 PDF——accept 是「按钮无法使用」的成因"
```

---

### Task 14: 手工验收

**Files:** 无（只跑，不改）

- [x] **Step 1: 起服务**

```bash
set -a && . ./.env && set +a
export FR_CONTENT_DB="$PWD/build/content.sqlite"
.venv/bin/fr serve --reload --port 8765
```

- [x] **Step 2: 拿一份真的公司制度 .docx 传进去**

不要用测试里那份五行的假文档。真实制度里有目录页、附录、表格、页眉——
那些正是切分会出错的地方。

- [x] **Step 3: 对着预览页逐条核对前十条**

看的是三件事：
1. 正文的头尾对不对（切歪的表现）
2. 编号是不是原文里的（模型自作主张编号的表现）
3. `⚠` 报出来的未覆盖行，是真的没条款（目录页），还是漏了条款

- [x] **Step 4: 确认导入，再去条款页看一条**

正文必须和你 Word 里那几行**一模一样**。有一个字不同就是 bug，回 Task 3。

- [x] **Step 5: 把结论写下来**

```bash
.venv/bin/fr usage --note "用 AI 导入了《XXX 管理办法》。切出 N 条，
其中对的 M 条。切错的主要形式是……"
```

这份 spec 的一期目标就是回答「AI 切分到底准不准」——那个答案决定二期
（扫描件 OCR）值不值得做。

---

## 自审记录

**Spec 覆盖**：§0.1 不做扫描件 → Task 1 Step 4 的拒绝分支；§1 边界法 → Task 3、4、8；
§2.1 一次过/分块 → Task 7；§2.2 校验 → Task 5、6；§3 落盘 → Task 9；
§4 预检 → Task 10；§4.1 权限 → Task 10 Step 3；§5.1 accept → Task 13；
§5.2 预览页 → Task 11、12；§5.3 异步 → **见下**；§6 出网 → Task 10 Step 3 的
`PayloadGuard([])` 与全程 `.venv/bin/pytest -q`；§7 文件 → 文件结构表；
§8 测试 → 各任务。

**已知偏离**：**§5.3「异步」这一期按同步实现。** Task 10 里 `outline_document`
是在请求里跑完的。理由：一次过路径只发一次调用（几秒），而分块路径要几分钟。
把 `jobs.py` 接进来会让 Task 10 从「一个路由」变成「一个路由 + 一种任务 +
一个轮询页 + 一套状态机」，而**一期的目标是尽快回答「切得准不准」**。

**这是一个真实的取舍，执行时如果第一份真实文档就超过 `ONE_SHOT_MAX_CHARS`，
立刻停下来补 Task 10.5（接 `jobs.py`），不要硬扛。** 判断依据：
Task 14 Step 2 那份文档如果 `plan_calls()` 返回超过 1 块，就补。
