"""表头认不出来时，让模型看一眼这张表长什么样。见 2026-08-25 AI 导入设计

**只在确定性解析失败时才走这里。** 表头在第一行、列名认得出来，那条路是
免费的、瞬时的、不会错——没理由换成一次模型调用。

**和文档那条路同一个原则：模型指位置，代码取原文。** 它回的是行号和列号，
不是内容，物理上改不了用户表里的字。

第二种回答是「这压根不是一张表」：一份制度贴进 Excel、一行一段，没有
编号/标题这种列。硬凑列映射会得到一堆垃圾条款，该走文档管线。
"""
import json
import re
from dataclasses import dataclass

from framework_reader.userframework.outline import Span, fill_gaps

# 样本给模型看结构，不是给它看内容。单元格截断到这个长度。
_CELL_SAMPLE = 40
_SAMPLE_ROWS = 15
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


@dataclass(frozen=True)
class TableShape:
    kind: str                       # table | document
    sheet: str = ""                 # 工作表名。空 = 只有一张，或没指定
    header_row: int = 0             # 1-based
    id_col: int = -1                # 0-based
    label_col: int = -1
    parent_col: int | None = None
    body_col: int | None = None
    why: str = ""


def sample_for_model(rows: list[list[str]], limit: int = _SAMPLE_ROWS) -> str:
    """带行号列号的前几行。模型要指位置，就得看得见位置。"""
    out = []
    for number, row in enumerate(rows[:limit], start=1):
        cells = " │ ".join(
            f"C{index}:{str(cell).strip()[:_CELL_SAMPLE]}"
            for index, cell in enumerate(row) if str(cell).strip())
        out.append(f"R{number} {cells}")
    return "\n".join(out)


def sample_sheets(sheets: list[tuple[str, list[list[str]]]],
                  limit: int = _SAMPLE_ROWS) -> str:
    """整个工作簿的样本。**每一张表都给**——「说明页在前、真表在后」是
    最常见的排法，只给第一张等于让模型替我们的疏忽背锅。
    """
    return "\n\n".join(
        f'=== Sheet "{name}" ===\n{sample_for_model(rows, limit)}'
        for name, rows in sheets
    )


def sheets_to_text(sheets: list[tuple[str, list[list[str]]]]) -> str:
    """当文档处理时，把整个工作簿摊成文本。

    表名留着——「附录」和「正文」是两回事，糊成一片就分不出来了。
    """
    return "\n".join(
        (f"{name}\n{rows_to_text(rows)}" if name else rows_to_text(rows))
        for name, rows in sheets
    )


def rows_to_text(rows: list[list[str]]) -> str:
    """当文档处理时，把每一格按阅读顺序摊成文本。

    空格子跳过，但**不整行跳过**——合并单元格常常只在第一列有字。
    """
    lines = []
    for row in rows:
        for cell in row:
            text = str(cell).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def parse_shape(raw: str) -> tuple[TableShape | None, str]:
    """解析模型的回答。**从不抛异常**——调用方是一个上传请求。"""
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
    """一条都不信模型。越界、重复用同一列、表头下面没数据、指了不存在的
    工作表，全拒。"""
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
    """按模型给的下标逐格取值，拼成预览页要的 (原文快照, 条款边界)。

    **正文快照就是各行正文拼起来的那份**，条款的行号区间正好落在自己那几行。
    这样预览与落库走的还是 `slice_lines`，和文档那条路一个机制——
    正文逐字来自原表，中间没有第二份副本。
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
            continue                    # 整行是空的
        # 有字但没编号没标题（表尾的「以上」、备注行）：**留着，不静默丢**。
        # 预览页会因为标题为空默认不勾它，人一眼看得见、自己决定删不删。
        body_lines = body.splitlines() if body else []
        start = len(lines) + 1
        lines.extend(body_lines)
        spans.append(Span(
            ref=ref, label=label,
            parent=cell(row, shape.parent_col) or None,
            start=start, end=start + len(body_lines) - 1,
        ))
    # 表格里也有空格子。补编号与标题，理由和文档那条路一样。
    return "\n".join(lines), fill_gaps(spans, lines)
