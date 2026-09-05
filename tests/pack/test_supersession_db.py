"""取代关系的落库与端点校验。spec §8②"""
import sqlite3

import pytest

from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_supersessions,
)
from framework_reader.pack.validate import validate_graph
from framework_reader.schema.entities import (
    ControlStatus,
    Framework,
    FrameworkControl,
    LicenseTier,
    SupersedeRelation,
    Supersession,
)


def _control(cid: str, status: ControlStatus = ControlStatus.ACTIVE) -> FrameworkControl:
    return FrameworkControl(
        id=cid, framework_id="NIST-CSF-2.0", label=cid, label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE, status=status,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    insert_frameworks(c, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(c, [
        _control("NIST-CSF-2.0:DE.CM-04", ControlStatus.DEPRECATED),
        _control("NIST-CSF-2.0:DE.CM-01"),
        _control("NIST-CSF-2.0:DE.CM-09"),
    ])
    yield c
    c.close()


def test_many_to_many_rows_round_trip(conn):
    insert_supersessions(conn, [
        Supersession(old_id="NIST-CSF-2.0:DE.CM-04", new_id="NIST-CSF-2.0:DE.CM-01",
                     relation=SupersedeRelation.INCORPORATED_INTO),
        Supersession(old_id="NIST-CSF-2.0:DE.CM-04", new_id="NIST-CSF-2.0:DE.CM-09",
                     relation=SupersedeRelation.INCORPORATED_INTO),
    ])
    rows = conn.execute(
        "SELECT old_id, new_id, relation FROM control_supersession ORDER BY new_id"
    ).fetchall()
    assert rows == [
        ("NIST-CSF-2.0:DE.CM-04", "NIST-CSF-2.0:DE.CM-01", "incorporated_into"),
        ("NIST-CSF-2.0:DE.CM-04", "NIST-CSF-2.0:DE.CM-09", "incorporated_into"),
    ]


def test_dangling_supersession_endpoint_is_reported(conn):
    insert_supersessions(conn, [Supersession(
        old_id="NIST-CSF-2.0:DE.CM-04", new_id="NIST-CSF-2.0:GONE",
        relation=SupersedeRelation.MOVED_TO,
    )])
    kinds = {i.kind for i in validate_graph(conn)}
    assert "dangling_supersession_endpoint" in kinds


def test_deprecated_control_without_mappings_is_not_an_orphan(conn):
    """废止条目本来就不该有映射边——不能让它们淹没 orphan 警告。"""
    issues = [i for i in validate_graph(conn) if i.kind == "orphan_control"]
    assert "NIST-CSF-2.0:DE.CM-04" not in {i.detail for i in issues}
