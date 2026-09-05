"""设置里下载用户库备份。身份库不进这份文件。"""
import re
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier


def _content(path: Path) -> Path:
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()
    return path


@pytest.fixture
def solo(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    return TestClient(create_app(_content(tmp_path / "content.sqlite")))


def _csrf(page: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    return found.group(1) if found else ""


def test_settings_offers_a_backup_entry(solo):
    page = solo.get("/settings").text
    assert "/settings/backup" in page
    assert "Backup" in page


def test_the_backup_page_explains_what_is_in_the_file(solo):
    page = solo.get("/settings/backup").text
    assert "user.sqlite" in page or "用户库" in page
    assert "identity" in page or "passphrase" in page
    assert 'action="/settings/backup"' in page
    assert 'method="post"' in page.lower()


def _tables(blob: bytes, dest: Path) -> set[str]:
    dest.write_bytes(blob)
    conn = sqlite3.connect(dest)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    return names


def test_the_download_is_sqlite_and_has_no_accounts(solo, tmp_path):
    result = solo.post("/settings/backup", data={"csrf": _csrf(
        solo.get("/settings/backup").text)})
    assert result.status_code == 200
    assert result.content.startswith(b"SQLite format 3")
    disp = result.headers.get("content-disposition", "")
    assert "framework-reader-user-" in disp and disp.endswith('.sqlite"')
    names = _tables(result.content, tmp_path / "got.sqlite")
    assert "user_framework" in names
    assert "account" not in names
    assert "session" not in names


def test_an_imported_framework_is_in_the_backup(solo, tmp_path):
    solo.post(
        "/import",
        data={"framework_id": "ACME-1", "name": "ACME 制度"},
        files={"file": ("f.csv", BytesIO("编号,标题\n3.1,账号管理\n".encode()),
                        "text/csv")},
    )
    result = solo.post("/settings/backup")
    dest = tmp_path / "got.sqlite"
    dest.write_bytes(result.content)
    conn = sqlite3.connect(dest)
    ids = [r[0] for r in conn.execute("SELECT id FROM user_framework")]
    conn.close()
    assert ids == ["ACME-1"]


def test_the_download_is_audited(solo):
    solo.post("/settings/backup")
    events = [e["event"] for e in IdentityStore().audit(10)]
    assert "backup.download" in events


def _with_interp(path: Path) -> Path:
    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
        InterpretationState,
    )
    from framework_reader.pack.db import insert_controls, insert_interpretations
    from framework_reader.schema.entities import FrameworkControl

    conn = sqlite3.connect(path)
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    insert_interpretations(conn, [Interpretation(
        control_id="NIST-CSF-2.0:DE.CM-01", state=InterpretationState.DRAFT,
        fields={
            name: Field(value="防的是没人看网络" if name == "intent" else None,
                        basis=Basis.INFERRED)
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance())])
    conn.close()
    return path


def test_the_backup_page_lists_frameworks_that_have_interpretations(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    db = _with_interp(_content(tmp_path / "content.sqlite"))
    client = TestClient(create_app(db))
    page = client.get("/settings/backup").text
    assert "NIST CSF 2.0" in page
    assert 'action="/settings/backup/NIST-CSF-2.0/pdf"' in page
    assert "NIST-800-53" not in page


def test_the_backup_page_has_no_pdf_list_when_nothing_is_interpreted(solo):
    page = solo.get("/settings/backup").text
    assert "/pdf" not in page


def test_the_pdf_download_is_a_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app
    from pypdf import PdfReader

    db = _with_interp(_content(tmp_path / "content.sqlite"))
    client = TestClient(create_app(db))
    result = client.post("/settings/backup/NIST-CSF-2.0/pdf")
    assert result.status_code == 200
    assert result.content.startswith(b"%PDF")
    disp = result.headers.get("content-disposition", "")
    assert "NIST-CSF-2.0" in disp and disp.endswith('.pdf"')
    text = "\n".join(p.extract_text() or "" for p in PdfReader(
        BytesIO(result.content)).pages)
    assert "DE.CM-01" in text and "AI draft" in text


def test_a_framework_without_interpretations_is_404(solo):
    assert solo.post("/settings/backup/NIST-CSF-2.0/pdf").status_code == 404


def test_a_viewer_cannot_download_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="vic@acme.cn", password="pw-vic-vic-vic",
                            roles=("viewer",))
    db = _with_interp(_content(tmp_path / "content.sqlite"))
    client = TestClient(create_app(db), follow_redirects=False)
    client.post("/login", data={"email": "vic@acme.cn",
                                "password": "pw-vic-vic-vic"})
    page = client.get("/frameworks").text
    assert client.post("/settings/backup/NIST-CSF-2.0/pdf",
                       data={"csrf": _csrf(page)}).status_code == 403


def test_a_viewer_cannot_download(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="vic@acme.cn", password="pw-vic-vic-vic",
                            roles=("viewer",))
    client = TestClient(create_app(_content(tmp_path / "content.sqlite")),
                        follow_redirects=False)
    client.post("/login", data={"email": "vic@acme.cn",
                                "password": "pw-vic-vic-vic"})
    assert client.get("/settings/backup").status_code == 403
    page = client.get("/frameworks").text
    assert client.post("/settings/backup",
                       data={"csrf": _csrf(page)}).status_code == 403
    assert "/settings/backup" not in client.get("/settings").text
