"""深浅主题切换。

默认深色（苹果黑），顶栏图标切换，localStorage 记住选择；
首帧渲染前就定主题（防闪白）。发布手册的 THEME_CSS 不受影响。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db), follow_redirects=False)


def test_the_shell_defines_theme_before_first_paint(client):
    page = client.get("/login").text
    # 防闪脚本在 <style> 之后（首帧渲染前执行）。
    style_at = page.index("<style>")
    anti_flash = page.index('localStorage.getItem("fr-theme")')
    assert anti_flash > style_at
    assert "document.documentElement.dataset.theme=t" in page


def test_both_palettes_ship_and_dark_reclaims_the_media_query(client):
    page = client.get("/login").text
    # 深色挂 :not(...) 抵消 THEME_CSS 媒体查询的特异性，否则系统深色的
    # Mac 上苹果黑会被它那套青灰色压掉。
    assert ':root:not([data-theme="light"])' in page
    assert ':root[data-theme="light"]' in page
    # 发布手册的浅色令牌原样还在（两种主题互不覆盖对方的底）。
    assert "--ground:#F1F4F3" in page
    assert "--ground:#000" in page
    assert "--ground:#fff" in page


def test_the_toggle_button_is_on_the_bare_login_page(client):
    page = client.get("/login").text
    assert 'data-toggle-theme' in page
    assert 'aria-label="Toggle dark and light"' in page
    # 两个图标都在，显隐交给 CSS。
    assert "i-moon" in page and "i-sun" in page


def test_the_toggle_wires_click_to_theme_and_storage(client):
    page = client.get("/login").text
    assert "localStorage.setItem('fr-theme'" in page
    assert "dataset.theme === 'light' ? 'dark' : 'light'" in page
