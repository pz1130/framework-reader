import sqlite3

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import (
    ControlStatus,
    Framework,
    FrameworkControl,
    LicenseTier,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM", framework_id="NIST-CSF-2.0",
                         label="Continuous Monitoring", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.DP-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Withdrawn one", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE,
                         status=ControlStatus.DEPRECATED),
    ])
    conn.close()
    return path


def test_active_only_excludes_deprecated(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0")]
    assert "NIST-CSF-2.0:DE.DP-01" not in ids


def test_leaf_only_excludes_categories(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0", leaf_only=True)]
    assert ids == ["NIST-CSF-2.0:DE.CM-01"]


def test_results_are_sorted_by_id(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0")]
    assert ids == sorted(ids)
