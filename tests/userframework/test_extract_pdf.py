"""PDF 的文字层。见 2026-08-25 AI 导入设计 §2

**一期不收扫描件。** 抽不出文字就说清楚「这份 PDF 里没有文字，只有扫描图片」，
而不是把一串空白喂给模型——模型会照着空白编。

这里的 PDF 是**现造的真文件**：手工拼出对象表与文字流，让 pypdf 真的去解析它。
早先的写法是猴补 `page.extract_text`，那测的是补丁本身，不是抽取。
"""
import pytest

from framework_reader.userframework.extract import (
    SUPPORTED, UnsupportedDocument, extract, pdf_pages,
    strip_running_heads,
)


def make_pdf(pages: list[str]) -> bytes:
    """拼一份最小但合法的 PDF，每页一段文字。**只支持 latin-1**——

    中文要嵌字体、要 CMap，那是另一件事。抽取逻辑不关心字符集，
    所以这里用 ASCII 就够；中文那条路由 .docx 的测试覆盖。
    """
    offsets: list[int] = []
    out = bytearray(b"%PDF-1.4\n")
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(len(pages)))
    bodies = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(pages):
        stream = "".join(
            f"BT /F1 12 Tf 50 {760 - n * 20} Td ({line}) Tj ET\n"
            for n, line in enumerate(text.splitlines()))
        bodies.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + index * 2} 0 R >>")
        bodies.append(f"<< /Length {len(stream)} >>\nstream\n{stream}endstream")
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    start = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    return bytes(out)


def _blank_pdf(page_count: int = 1) -> bytes:
    """有页、没有任何文字流。图片型扫描件在抽取器眼里就长这样。"""
    return make_pdf([""] * page_count)


# ---------- 抽得出来 ----------

def test_the_text_really_comes_out_of_the_file():
    data = make_pdf(["5.1 Account Management\nOne account per person."])
    assert pdf_pages(data) == ["5.1 Account Management\nOne account per person."]


def test_each_page_comes_back_separately():
    """按页返回是为了下一步剔页眉页脚——那要靠「跨页重复」判断。"""
    pages = pdf_pages(make_pdf(["first page", "second page", "third page"]))
    assert len(pages) == 3
    assert "second" in pages[1]


def test_extract_joins_the_pages():
    text = extract("policy.pdf", make_pdf(["line one", "line two"]))
    assert "line one" in text and "line two" in text


def test_pdf_is_in_the_supported_list():
    assert ".pdf" in SUPPORTED


# ---------- 抽不出来 ----------

def test_a_pdf_with_no_text_layer_is_refused():
    """一期不 OCR。要说清楚是「没有文字」，不是给一串空白让人以为文档是空的。"""
    with pytest.raises(UnsupportedDocument) as exc:
        pdf_pages(_blank_pdf(3))
    assert "scanned" in str(exc.value)


def test_extract_refuses_it_too_not_just_pdf_pages():
    with pytest.raises(UnsupportedDocument):
        extract("scan.pdf", _blank_pdf(2))


def test_a_broken_pdf_says_so_instead_of_raising_a_stack_trace():
    """损坏的文件走到用户面前应该是一句话，不是一个堆栈。"""
    with pytest.raises(UnsupportedDocument) as exc:
        extract("x.pdf", b"not a pdf at all")
    assert "won't open" in str(exc.value)


def test_an_empty_upload_is_not_a_crash():
    with pytest.raises(UnsupportedDocument):
        extract("x.pdf", b"")


def test_one_page_with_text_among_blank_ones_is_enough():
    """扫描件是**整份**没有文字层。混排文档里有几页是插图，不该整份被拒。"""
    pages = pdf_pages(make_pdf(["", "5.1 Account Management", ""]))
    assert pages[1] == "5.1 Account Management"


# ---------- 页眉页脚 ----------
#
# 它们会落进条款正文里，而它们不是条款的一部分。
# **宁可留下也不误删**：页眉落进正文只是噪声，删掉一行正文是把用户的
# 制度改了，而他不会知道。

def test_a_line_repeated_on_every_page_is_a_running_head():
    pages = [
        "ACME Policy\n5.1 Account Management\nPage 1",
        "ACME Policy\n5.2 Password Policy\nPage 2",
        "ACME Policy\n5.3 Log Retention\nPage 3",
    ]
    got = strip_running_heads(pages)
    assert "ACME Policy" not in "\n".join(got)
    assert "5.1 Account Management" in got[0]


def test_page_numbers_go_too():
    """「第 1 页」每页都变，靠重复判断抓不到——按形状抓。"""
    pages = ["正文一\n第 1 页", "正文二\n第 2 页", "正文三\n第 3 页"]
    joined = "\n".join(strip_running_heads(pages))
    assert "第 1 页" not in joined
    assert "正文一" in joined


def test_bare_numbers_and_dashed_numbers_are_page_numbers_too():
    pages = ["正文一\n- 1 -", "正文二\n- 2 -", "正文三\n- 3 -"]
    joined = "\n".join(strip_running_heads(pages))
    assert "- 2 -" not in joined
    assert "正文二" in joined


def test_a_line_that_only_repeats_twice_in_a_long_document_survives():
    """「本节要求」在两页出现过，不代表它是页眉。误删正文比留下页眉糟得多。"""
    pages = ["本节要求\nA", "别的\nB", "本节要求\nC", "别的\nD",
             "别的\nE", "别的\nF"]
    joined = "\n".join(strip_running_heads(pages))
    assert joined.count("本节要求") == 2


def test_a_two_page_document_keeps_everything():
    """两页里的「重复」说明不了任何事，样本太小。"""
    pages = ["标题\nA", "标题\nB"]
    joined = "\n".join(strip_running_heads(pages))
    assert joined.count("标题") == 2


def test_a_head_missing_from_the_first_page_is_still_a_head():
    """首页常常没有页眉（封面）。阈值不是 100%，就是为了这个。"""
    pages = ["封面\n目录", "ACME Policy\n正文一", "ACME Policy\n正文二",
             "ACME Policy\n正文三", "ACME Policy\n正文四"]
    joined = "\n".join(strip_running_heads(pages))
    assert "ACME Policy" not in joined
    assert "正文一" in joined


def test_extract_strips_them_on_the_way_out():
    """管线里 extract() 是入口，剔除要发生在它里面，不能靠调用方记得。"""
    data = make_pdf([
        "ACME Policy\n5.1 Account Management\nPage 1",
        "ACME Policy\n5.2 Password Policy\nPage 2",
        "ACME Policy\n5.3 Log Retention\nPage 3",
    ])
    text = extract("policy.pdf", data)
    assert "ACME Policy" not in text
    assert "5.2 Password Policy" in text


# ---------- 康熙部首：看着一样，码位不同 ----------
#
# 实测（用户导入《人工智能安全治理框架 2.0》PDF）：抽出来的「人工智能」是
# U+2F08 康熙部首·人 + U+2F27 康熙部首·工，不是 U+4EBA / U+5DE5。
# 那份文档里这类字符出现 2236 处。看着一模一样，`fr search 人工智能`
# 一条都搜不到，起草时模型收到的是一堆怪字符。

def test_kangxi_radicals_become_the_real_characters():
    from framework_reader.userframework.extract import normalize_cjk

    assert normalize_cjk("⼈⼯智能") == "人工智能"
    assert normalize_cjk("⽹络安全") == "网络安全"
    assert normalize_cjk("⻛险") == "风险"


def test_full_width_punctuation_is_left_alone():
    """整段 NFKC 会把全角（）：；转成半角——那是在**改用户的正文**。

    中文制度里全角标点是正规写法，动它就违反了「落库的正文逐字等于原文」。
    """
    from framework_reader.userframework.extract import normalize_cjk

    assert normalize_cjk("（一）适用范围：本办法……") == "（一）适用范围：本办法……"
    assert normalize_cjk("ＡＢＣ１２３") == "ＡＢＣ１２３"


def test_normal_text_passes_through_untouched():
    from framework_reader.userframework.extract import normalize_cjk

    text = "公司应当为每一名员工分配唯一账号，禁止共用。\n离职当日停用。"
    assert normalize_cjk(text) == text


def test_extract_normalizes_on_the_way_out():
    """管线里 extract() 是入口，归一化要发生在它里面。"""
    data = make_pdf(["ordinary ascii"])
    assert extract("x.pdf", data) == "ordinary ascii"

    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("word/document.xml",
                        "<w:document><w:body><w:p><w:r><w:t>⼈⼯智能安全</w:t>"
                        "</w:r></w:p></w:body></w:document>")
    assert extract("x.docx", buffer.getvalue()) == "人工智能安全"


# ---------- 目录行 ----------

def test_a_table_of_contents_line_is_dropped():
    """`1.人工智能安全治理原则..........................` 是目录，不是条款。

    它进模型的 payload 只会占 token，还可能被切成假条款。
    """
    from framework_reader.userframework.extract import strip_toc_lines

    lines = ["目  录",
             "1.人工智能安全治理原则..................................",
             "2.技术应对措施........................................",
             "正文从这里开始。"]
    got = strip_toc_lines(lines)
    assert got == ["目  录", "正文从这里开始。"]


def test_a_chinese_ellipsis_in_real_text_survives():
    """「……」是正规中文标点，只有两个字符。目录的引导点是一长串。"""
    from framework_reader.userframework.extract import strip_toc_lines

    lines = ["本办法适用于研发、测试、运维……等全部环节。"]
    assert strip_toc_lines(lines) == lines


def test_a_sentence_with_a_few_dots_survives():
    from framework_reader.userframework.extract import strip_toc_lines

    lines = ["见附件 3.2.1 与 4.5.6 的说明。"]
    assert strip_toc_lines(lines) == lines


def test_a_toc_line_with_a_page_number_goes_too():
    from framework_reader.userframework.extract import strip_toc_lines

    lines = ["3.1 技术内生安全风险...............................12"]
    assert strip_toc_lines(lines) == []
