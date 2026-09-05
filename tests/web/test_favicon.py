"""Browser tab mark. /favicon.ico is requested with no session; it must be public."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier
from framework_reader.web import views


def _content(path):
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    return path


@pytest.fixture
def locked(tmp_path, monkeypatch):
    """Identity is on, no session cookie — the case a fresh browser tab hits."""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    IdentityStore().create_account(
        email="boss@acme.cn", password="pw-boss-boss", roles=("admin",))
    return TestClient(create_app(_content(tmp_path / "content.sqlite")),
                      follow_redirects=False)


def test_the_shell_points_at_the_product_mark():
    html = views.page("标题", "<p>正文</p>", csrf="tok", who="谁")
    assert 'rel="icon" href="/favicon.svg"' in html
    assert 'rel="icon" href="/favicon.ico"' in html
    assert 'rel="apple-touch-icon" href="/apple-touch-icon.png"' in html
    assert "Framework Workbench" in html
    assert "brandmark" not in html


def test_a_locked_tab_can_fetch_the_favicon_without_signing_in(locked):
    svg = locked.get("/favicon.svg")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in svg.content
    ico = locked.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.content[:4] == b"\x00\x00\x01\x00"
    png = locked.get("/apple-touch-icon.png")
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    login = locked.get("/login")
    assert login.status_code == 200
    assert 'rel="icon" href="/favicon.svg"' in login.text
    assert "Framework Workbench" in login.text
    assert "brandmark" not in login.text
