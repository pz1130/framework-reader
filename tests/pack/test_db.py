import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_mappings,
    insert_unified,
)
from framework_reader.schema.entities import (
    Framework,
    FrameworkControl,
    LicenseTier,
    UnifiedControl,
)
from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)
from framework_reader.schema.sources import DisallowedSourceError, SourceRegistry

REGISTRY = SourceRegistry.load(Path("content/allowed_sources.yaml"))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    yield c
    c.close()


def test_original_text_table_exists_and_is_empty(conn):
    """原文表必须存在（供用户本地注入）但构建产物中为空。spec §3.2②"""
    rows = conn.execute("SELECT COUNT(*) FROM original_text").fetchone()
    assert rows[0] == 0


def test_insert_and_read_back_control(conn):
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="https://www.nist.gov/cyberframework",
        license_note="US Government work, public domain",
    )])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0", parent_id=None,
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE,
    )])
    row = conn.execute(
        "SELECT label, status FROM framework_control WHERE id = ?",
        ("NIST-CSF-2.0:DE.CM-01",),
    ).fetchone()
    assert row == ("Networks are monitored", "active")


def test_mapping_with_disallowed_source_is_rejected(conn):
    """来源白名单断言必须在写库这一层拦截。spec §10.A"""
    bad = Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01",
        to_id="ISO-27001-2022:A.8.16",
        relation=Relation.RELATED,
        provenance=Provenance(
            level=ProvenanceLevel.L2_PUBLIC, source="SCF-2026.1", source_version="2026.1"
        ),
        note="",
    )
    with pytest.raises(DisallowedSourceError, match="SCF"):
        insert_mappings(conn, [bad], REGISTRY)
    assert conn.execute("SELECT COUNT(*) FROM mapping").fetchone()[0] == 0


def test_unified_control_roundtrip_keeps_locale(conn):
    insert_unified(conn, [UnifiedControl(id="UC:DE.CM-01", label="网络监控", locale="zh-CN")])
    assert conn.execute("SELECT locale FROM unified_control").fetchone()[0] == "zh-CN"


def test_mapping_stores_provenance_fields(conn):
    m = Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01",
        to_id="NIST-800-53-R5:SI-4",
        relation=Relation.RELATED,
        provenance=Provenance(
            level=ProvenanceLevel.L3_CONFIRMED,
            source="authored:framework-reader",
            source_version="2026.08",
            confirmed_by="author",
            confirmed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        ),
        note="CSF 说 outcome，800-53 说具体控制",
    )
    insert_mappings(conn, [m], REGISTRY)
    row = conn.execute(
        "SELECT level, source, confirmed_by, note FROM mapping"
    ).fetchone()
    assert row[0] == "L3_CONFIRMED"
    assert row[1] == "authored:framework-reader"
    assert row[2] == "author"
    assert "outcome" in row[3]
