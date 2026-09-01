"""框架都收进 /frameworks 一页：内置一段、导入的一段。

主页直接跳过来——这个工作台干的就是「挑一个框架进去干活」，
不需要一个仪式性的首页。
"""
import re
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier

BUILTIN = "NIST-CSF-2.0"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=BUILTIN, name="NIST Cybersecurity Framework 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db), follow_redirects=False)


def _import(client, framework_id="ACME-1", name="ACME 制度"):
    return client.post(
        "/import",
        data={"framework_id": framework_id, "name": name},
        files={"file": ("f.csv", BytesIO("编号,标题\n3.1,账号管理\n".encode()),
                        "text/csv")})


def test_the_title_link_lands_on_the_frameworks_page(client):
    """顶栏那个「框架工作台」点了要到得了地方，不能停在一个跳转上。"""
    page = client.get("/frameworks").text
    assert 'href="/"' in page or 'href="/frameworks"' in page


# ---------- 两段都在 ----------

def test_the_builtin_frameworks_are_cards(client):
    page = client.get("/frameworks").text
    assert "NIST Cybersecurity Framework 2.0" in page
    # 卡片上还挂了滚动渐现的 .reveal 类，所以只断前缀。
    assert '<a class="card' in page


def test_the_imported_ones_are_a_table_below(client):
    """这一段是会长的那一段——卡片十几个就没法看，表格一百行还能翻。"""
    _import(client)
    page = client.get("/frameworks").text
    table = re.search(r"<table>.*?</table>", page, re.S)
    assert table and "ACME 制度" in table.group(0)


def test_the_two_sections_are_labelled(client):
    _import(client)
    page = client.get("/frameworks").text
    assert "Built-in" in page and "My imports" in page


def test_the_imported_section_is_absent_when_there_are_none(client):
    """一个都没导入时挂一张空表，只是噪声。"""
    page = client.get("/frameworks").text
    assert "<table>" not in page


def test_the_frameworks_page_does_not_carry_the_import_form(client):
    """框架页是目录。导入走顶栏——不在框架页里再叠一个表单。"""
    page = client.get("/frameworks").text
    assert 'action="/import"' not in page
    assert "Import your own framework" not in page


def test_the_imported_row_still_carries_when_and_from_what(client):
    _import(client)
    page = client.get("/frameworks").text
    assert "f.csv" in page and "2026" in page


def test_the_delete_link_is_still_there(client):
    _import(client)
    assert "/f/ACME-1/delete" in client.get("/frameworks").text


# ---------- /mine 不留两个说法 ----------

def test_the_old_mine_url_still_works(client):
    """收藏夹里可能存着它。别让人撞一个 404。"""
    _import(client)
    result = client.get("/mine")
    assert result.status_code in (303, 200)
    if result.status_code == 303:
        assert result.headers["location"] == "/frameworks"


def test_the_frameworks_page_has_no_switcher(client):
    """目录就在这一页上，顶上再摊一排是重复，而且会折行。"""
    _import(client)
    assert '<div class="tabs">' not in client.get("/frameworks").text
