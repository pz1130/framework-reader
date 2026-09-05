import json
import sqlite3

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.pack.id_baseline import check_baseline, write_baseline
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


def _ctl(cid: str) -> FrameworkControl:
    return FrameworkControl(
        id=cid, framework_id="NIST-CSF-2.0", label="x", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    insert_frameworks(c, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0", tier=LicenseTier.A_EMBEDDABLE,
        source_url="u", license_note="pd")])
    yield c
    c.close()


def test_no_drift_when_ids_unchanged(tmp_path, conn):
    insert_controls(conn, [_ctl("NIST-CSF-2.0:DE.CM-01")])
    path = write_baseline(conn, tmp_path / "b.json")
    assert check_baseline(conn, path) == []


def test_adding_ids_is_allowed(tmp_path, conn):
    insert_controls(conn, [_ctl("NIST-CSF-2.0:DE.CM-01")])
    path = write_baseline(conn, tmp_path / "b.json")
    insert_controls(conn, [_ctl("NIST-CSF-2.0:DE.CM-02")])
    assert check_baseline(conn, path) == []


def test_disappearing_id_is_reported(tmp_path, conn):
    insert_controls(conn, [_ctl("NIST-CSF-2.0:DE.CM-01")])
    path = tmp_path / "b.json"
    path.write_text(json.dumps(
        ["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:GONE-01"]), encoding="utf-8")
    assert check_baseline(conn, path) == ["NIST-CSF-2.0:GONE-01"]


def test_missing_baseline_file_reports_nothing(tmp_path, conn):
    assert check_baseline(conn, tmp_path / "absent.json") == []
