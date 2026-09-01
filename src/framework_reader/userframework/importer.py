"""吃 CSV / XLSX，吐出 (编号, 标题, 上级) 三元组。主 spec §7.3.5

安全团队手上的东西就是 Excel，所以不发明新格式。
**坏行一律报错并指出行号，绝不静默跳过**——静默跳过的结果是用户以为全导进去了。
"""
import csv
from pathlib import Path

_ID_HEADERS = {"编号", "控制编号", "条号", "id", "control_id", "ref", "ref_id"}
_LABEL_HEADERS = {"标题", "名称", "控制", "条款", "label", "name", "title"}
_PARENT_HEADERS = {"上级", "父级", "上级编号", "parent", "parent_id"}
# 用户自己公司的制度原文。他的文档、他的机器、他的 key——可以拿去起草。
# 与 Tier C/D 的受版权标准原文完全是两回事，后者永远不许出网（主 spec §9）。
_BODY_HEADERS = {"正文", "描述", "要求", "内容", "条款正文", "body", "text", "description"}


class ImportError_(Exception):
    """导入失败。消息要能让用户自己改好表，所以一律带行号或列名。"""


def read_sheets(path: Path) -> list[tuple[str, list[list[str]]]]:
    """一个工作簿里的**每一张表**，带名字。

    `book.active` 只读一张，而「说明页在前、真表在后」是最常见的排法——
    实测一份检查表工作簿的活动表是「Get started」，18 行全是使用说明，
    真正的检查表在别的 sheet 里。只读一张的结果是整份文件白导。
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            return [("", [list(row) for row in csv.reader(handle)])]
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        return [
            (sheet.title, [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ])
            for sheet in book.worksheets
        ]
    raise ImportError_(f"Unknown file type {suffix} - supported: .csv and .xlsx")


def read_rows(path: Path) -> list[list[str]]:
    """第一张表。CLI 与既有调用方还在用它。"""
    sheets = read_sheets(path)
    return sheets[0][1] if sheets else []


def parse_any_sheet(
    sheets: list[tuple[str, list[list[str]]]]
) -> tuple[str | None, list[tuple[str, str, str | None, str]] | None]:
    """挨个试，第一张解析得通的赢。全不通就回 (None, None)——
    那时候该让模型看一眼整个工作簿（见 `web/app.py` 的 `_shape_table`）。
    """
    for name, rows in sheets:
        try:
            return name, parse_table(rows)
        except ImportError_:
            continue
    return None, None


def _column(header: list[str], names: set[str], what: str, required: bool = True,
            rows: list[list[str]] | None = None) -> int | None:
    for index, cell in enumerate(header):
        if str(cell).strip().lower() in names:
            return index
    if required:
        raise ImportError_(_missing_header_message(what, names, header, rows or []))
    return None


# 往下找几行。中文单位的表格常常在表头上面压一行标题或一片合并单元格，
# 再多就不是「表头靠下」而是别的问题了。
_HEADER_SEARCH_ROWS = 10


def _missing_header_message(what: str, names: set[str],
                            header: list[str], rows: list[list[str]]) -> str:
    """**说出它看见了什么。** 只说「找不到编号这一列」，而人看着自己的表
    明明有「编号」两个字，他只会觉得这工具坏了。
    """
    seen = "、".join(str(c).strip() for c in header if str(c).strip()) or "(empty)"
    for number, row in enumerate(rows[1:_HEADER_SEARCH_ROWS], start=2):
        if any(str(cell).strip().lower() in names for cell in row):
            return (
                f'Column "{what}" not found in the header - the first row is: {seen[:60]}.'
                # 这句话会被 HTML 转义后渲到页面上，写 markdown 只会原样显示星号。
                f"The real header looks like it is on row {number}, "
                "delete the rows above it and upload again."
            )
    accepted = ", ".join(sorted(n for n in names if not n.isascii()))
    return (
        f'Column "{what}" not found in the header. The first row is: {seen[:60]}.'
        f'This column can be named: {accepted}. The first row also needs "Title" ("Parent" and "Body" optional).'
    )
    return None


def parse_table(rows: list[list[str]]) -> list[tuple[str, str, str | None, str]]:
    if not rows:
        raise ImportError_("The file is empty")
    header = rows[0]
    id_col = _column(header, _ID_HEADERS, "编号", rows=rows)
    label_col = _column(header, _LABEL_HEADERS, "标题", rows=rows)
    parent_col = _column(header, _PARENT_HEADERS, "上级", required=False)
    body_col = _column(header, _BODY_HEADERS, "正文", required=False)

    out: list[tuple[str, str, str | None, str]] = []
    seen: set[str] = set()
    for number, row in enumerate(rows[1:], start=2):
        def cell(index: int | None) -> str:
            if index is None or index >= len(row):
                return ""
            return str(row[index]).strip()

        local, label, parent = cell(id_col), cell(label_col), cell(parent_col)
        if not local and not label:
            continue          # 整行空白，跳过
        if not local:
            raise ImportError_(f"Row {number} has no number (title: '{label[:20]}')")
        if not label:
            raise ImportError_(f"Row {number} has no title (number: {local})")
        if local in seen:
            raise ImportError_(f"Row {number} has duplicate number {local}")
        seen.add(local)
        out.append((local, label, parent or None, cell(body_col)))

    if not out:
        raise ImportError_("No data rows below the header")

    known = {local for local, _label, _parent, _body in out}
    for local, _label, parent, _body in out:
        if parent and parent not in known:
            raise ImportError_(f"{local} has parent {parent} which is not in the table")
    return out
