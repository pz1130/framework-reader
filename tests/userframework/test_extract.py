"""从上传的文档里取文本、切段。见网页服务化设计 §8 S5"""
import io
import zipfile

import pytest

from framework_reader.userframework.extract import (
    UnsupportedDocument, chunk, extract,
)


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
           f"{body}</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_a_text_file_comes_through(): 
    assert "日志留存" in extract("制度.txt", "日志留存六个月".encode("utf-8"))


def test_a_gb18030_file_is_not_mojibake():
    """中文团队的老文档十有八九是 GBK 存的。"""
    assert "日志留存" in extract("制度.txt", "日志留存六个月".encode("gb18030"))


def test_a_docx_comes_through():
    text = extract("制度.docx", _docx(["第一章 日志管理", "日志留存不少于六个月。"]))
    assert "日志留存不少于六个月。" in text
    assert "<w:" not in text


def test_a_docx_keeps_paragraphs_apart():
    text = extract("制度.docx", _docx(["第一条 甲", "第二条 乙"]))
    assert "第一条 甲\n第二条 乙" in text


def test_a_renamed_doc_says_what_to_do():
    with pytest.raises(UnsupportedDocument) as caught:
        extract("制度.docx", b"not a zip at all")
    assert "Re-save it as .docx" in str(caught.value)


def test_a_docx_with_an_excessive_expanded_document_is_rejected(monkeypatch):
    import framework_reader.userframework.extract as module

    monkeypatch.setattr(module, "MAX_DOCX_XML_BYTES", 32)
    with pytest.raises(UnsupportedDocument) as caught:
        extract("制度.docx", _docx(["正文" * 100]))
    assert "expands past" in str(caught.value)


def test_a_pdf_is_refused_with_a_reason(): 
    """从 PDF 切出来的段落是乱的。噪声接地比没有接地更糟。"""
    with pytest.raises(UnsupportedDocument) as caught:
        extract("制度.pdf", b"%PDF-1.4")
    assert "PDF" in str(caught.value)


def test_an_executable_is_not_a_document():
    with pytest.raises(UnsupportedDocument):
        extract("payload.exe", b"MZ")


# ---------- 切段 ----------

def test_a_heading_becomes_the_label_of_what_follows():
    parts = chunk("第一章 日志管理\n日志留存不少于六个月。\n")
    assert parts[0][0] == "第一章 日志管理"
    assert "六个月" in parts[0][1]


def test_a_requirement_is_not_cut_in_half():
    """半句话是接地材料最怕的东西——模型会把它补完，补出来的正是幻觉。"""
    text = "第一条\n" + "日志留存不少于六个月，且每季度复核一次。" * 3
    parts = chunk(text)
    assert all(p[1].endswith("。") for p in parts)


def test_an_empty_document_yields_nothing():
    assert chunk("   \n\n  ") == []


def test_a_long_document_is_split_into_several(): 
    text = "\n\n".join("这是一段制度正文，讲的是访问控制。" * 20 for _ in range(4))
    assert len(chunk(text)) > 1
