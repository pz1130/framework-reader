"""同一个组织里两个人同时导入，不该互相覆盖。

原先上传落在一个**固定文件名** `_upload{后缀}` 上——单人本机时无害，
一旦是多人共用的网页服务，两个人同时点导入就是一个人的表被另一个人覆盖掉。
（托管服务化设计 §5 #2）
"""
import sqlite3
import threading
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(path))


def _upload(client, framework_id: str, label: str):
    body = f"编号,标题,正文\n1.1,{label},正文\n".encode()
    return client.post(
        "/import",
        data={"framework_id": framework_id, "name": f"{framework_id} 的制度"},
        files={"file": ("f.csv", BytesIO(body), "text/csv")},
        follow_redirects=False,
    )


def test_two_people_importing_at_once_do_not_clobber_each_other(client):
    errors: list[Exception] = []

    def go(fid, label):
        try:
            for _ in range(6):
                _upload(client, fid, label)
        except Exception as exc:                      # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=go, args=("A-SEC", "甲写的条款")),
        threading.Thread(target=go, args=("B-SEC", "乙写的条款")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert "甲写的条款" in client.get("/c/A-SEC:1.1").text
    assert "乙写的条款" in client.get("/c/B-SEC:1.1").text


def test_the_scratch_files_are_cleaned_up(client, tmp_path):
    """临时文件用完要删。不删的话它们会在用户库旁边越堆越多。"""
    _upload(client, "A-SEC", "条款")
    scratch = tmp_path / "home" / "_uploads"
    assert not scratch.exists() or list(scratch.iterdir()) == []
