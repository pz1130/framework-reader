"""表头认不出来时，让模型看一眼这张表长什么样。

**和文档那条路同一个原则：模型指位置，代码取原文。** 它回的是行号和列号，
不是内容——物理上改不了用户表里的字。

第二种回答是「这压根不是一张表」：一份制度贴进 Excel、一行一段，
没有编号/标题这种列。那时候硬凑列映射是错的，该走文档管线。
"""
import pytest

from framework_reader.userframework.table_ai import (
    TableShape, parse_shape, rows_to_text, sample_for_model, to_draft,
    validate_shape,
)

ROWS = [
    ["ACME 信息安全控制清单", "", "", ""],
    ["版本 2.0　制表：安全部", "", "", ""],
    ["控制编号", "控制名称", "上级", "要求正文"],
    ["3.1", "账号管理", "", "公司应当为每一名员工分配唯一账号。\n禁止共用。"],
    ["3.1.1", "账号申请", "3.1", "由部门主管提交申请。"],
]

TABLE_REPLY = ('{"kind":"table","header_row":3,"id_col":0,"label_col":1,'
               '"parent_col":2,"body_col":3}')


# ---------- 发给模型的样本 ----------

def test_the_sample_carries_row_and_column_numbers():
    """模型要指位置，就得看得见位置。"""
    sample = sample_for_model(ROWS)
    assert "R3" in sample and "C0" in sample


def test_the_sample_is_capped_so_a_huge_sheet_does_not_blow_the_payload():
    big = [[f"第{n}行", "x"] for n in range(1, 500)]
    sample = sample_for_model(big, limit=15)
    assert "第15行" in sample
    assert "第16行" not in sample


def test_long_cells_are_truncated_in_the_sample():
    """样本只为让模型认出结构。整段正文塞进去只是烧 token。"""
    rows = [["编号", "正文"], ["3.1", "啊" * 500]]
    assert len(sample_for_model(rows)) < 1000


# ---------- 解析模型的回答 ----------

def test_a_table_answer_parses():
    shape, error = parse_shape(TABLE_REPLY)
    assert error == ""
    assert shape.kind == "table"
    assert shape.header_row == 3
    assert (shape.id_col, shape.label_col) == (0, 1)
    assert (shape.parent_col, shape.body_col) == (2, 3)


def test_a_document_answer_parses():
    shape, error = parse_shape(
        '{"kind":"document","why":"这不是控制清单，是一份连续正文"}')
    assert error == ""
    assert shape.kind == "document"
    assert "连续正文" in shape.why


def test_a_markdown_fence_is_peeled():
    shape, error = parse_shape("```json\n" + TABLE_REPLY + "\n```")
    assert error == "" and shape.kind == "table"


def test_optional_columns_may_be_null():
    shape, _ = parse_shape('{"kind":"table","header_row":1,"id_col":0,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    assert shape.parent_col is None and shape.body_col is None


def test_garbage_is_an_error_not_a_crash():
    shape, error = parse_shape("我看不懂这张表")
    assert shape is None and error


def test_an_unknown_kind_is_an_error():
    shape, error = parse_shape('{"kind":"spreadsheet","header_row":1}')
    assert shape is None and error


# ---------- 校验：一条都不信模型 ----------

def test_a_header_row_past_the_end_is_refused():
    shape, _ = parse_shape('{"kind":"table","header_row":99,"id_col":0,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    checked, error = validate_shape(shape, ROWS)
    assert checked is None
    assert "99" in error


def test_a_column_past_the_widest_row_is_refused():
    shape, _ = parse_shape('{"kind":"table","header_row":3,"id_col":9,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    checked, error = validate_shape(shape, ROWS)
    assert checked is None


def test_the_same_column_used_twice_is_refused():
    """编号和标题指到同一列，落库出来两列一模一样。"""
    shape, _ = parse_shape('{"kind":"table","header_row":3,"id_col":1,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    checked, error = validate_shape(shape, ROWS)
    assert checked is None


def test_a_header_row_with_no_data_under_it_is_refused():
    """表头指到最后一行，下面一条数据都没有。"""
    shape, _ = parse_shape('{"kind":"table","header_row":5,"id_col":0,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    checked, error = validate_shape(shape, ROWS)
    assert checked is None


def test_a_document_answer_needs_no_column_checks():
    shape, _ = parse_shape('{"kind":"document","why":"一行一段"}')
    checked, error = validate_shape(shape, ROWS)
    assert checked is shape and error == ""


# ---------- 取值：逐格来自原表 ----------

def test_the_values_come_out_of_the_cells_verbatim():
    """**地基。** 模型只给了下标，字是代码从原表取的。"""
    shape, _ = parse_shape(TABLE_REPLY)
    text, spans = to_draft(ROWS, shape)
    from framework_reader.userframework.outline import slice_lines

    assert spans[0].ref == "3.1"
    assert spans[0].label == "账号管理"
    assert slice_lines(text, spans[0].start, spans[0].end) == (
        "公司应当为每一名员工分配唯一账号。\n禁止共用。")


def test_the_parent_column_is_carried_through():
    shape, _ = parse_shape(TABLE_REPLY)
    _, spans = to_draft(ROWS, shape)
    assert spans[1].parent == "3.1"
    assert spans[0].parent is None


def test_rows_above_the_header_are_not_data():
    shape, _ = parse_shape(TABLE_REPLY)
    _, spans = to_draft(ROWS, shape)
    assert len(spans) == 2
    assert all("ACME" not in s.label for s in spans)


def test_a_row_with_no_id_and_no_label_is_skipped():
    """表格末尾常有空行、合计行。"""
    rows = ROWS + [["", "", "", ""], ["", "", "", "以上"]]
    shape, _ = parse_shape(TABLE_REPLY)
    _, spans = to_draft(rows, shape)
    assert len(spans) == 3          # 全空那行跳过，「以上」那行留着让人自己删


def test_a_table_with_no_body_column_yields_empty_bodies():
    """只有编号和标题的清单。正文为空是正常的——预览页会照实说。"""
    shape, _ = parse_shape('{"kind":"table","header_row":3,"id_col":0,'
                           '"label_col":1,"parent_col":null,"body_col":null}')
    text, spans = to_draft(ROWS, shape)
    assert len(spans) == 2
    assert all(s.end < s.start for s in spans)


# ---------- 不是表的那条路 ----------

def test_flattening_keeps_every_cell_in_reading_order():
    text = rows_to_text([["第一条", "为规范管理"], ["", "制定本办法。"]])
    assert "为规范管理" in text and "制定本办法。" in text
    assert text.index("为规范管理") < text.index("制定本办法。")


def test_flattening_drops_empty_cells_not_whole_rows():
    text = rows_to_text([["", "正文一", ""], ["", "", ""], ["正文二", "", ""]])
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines == ["正文一", "正文二"]


# ---------- 一个工作簿里有好几张表 ----------

SHEETS = [
    ("Get started", [["使用说明"], ["先看这里"]]),
    ("Checklist", [["控制编号", "控制名称"], ["3.1", "账号管理"]]),
]


def test_the_sample_names_every_sheet():
    """模型要能指「哪一张表」，就得看得见表名。"""
    from framework_reader.userframework.table_ai import sample_sheets

    sample = sample_sheets(SHEETS)
    assert "Get started" in sample and "Checklist" in sample


def test_the_sample_covers_more_than_the_first_sheet():
    from framework_reader.userframework.table_ai import sample_sheets

    assert "控制编号" in sample_sheets(SHEETS)


def test_a_shape_can_name_its_sheet():
    shape, error = parse_shape(
        '{"kind":"table","sheet":"Checklist","header_row":1,'
        '"id_col":0,"label_col":1,"parent_col":null,"body_col":null}')
    assert error == "" and shape.sheet == "Checklist"


def test_a_sheet_that_does_not_exist_is_refused():
    from framework_reader.userframework.table_ai import validate_shape

    shape, _ = parse_shape(
        '{"kind":"table","sheet":"没这张表","header_row":1,'
        '"id_col":0,"label_col":1,"parent_col":null,"body_col":null}')
    checked, error = validate_shape(shape, SHEETS[1][1], sheet_names=["Checklist"])
    assert checked is None
    assert "没这张表" in error


def test_flattening_a_workbook_keeps_the_sheet_names():
    """当文档处理时，表名是有信息的——「附录」和「正文」不该糊成一片。"""
    from framework_reader.userframework.table_ai import sheets_to_text

    text = sheets_to_text(SHEETS)
    assert "Get started" in text and "使用说明" in text and "账号管理" in text
