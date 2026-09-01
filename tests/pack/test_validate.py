import json
import sqlite3
from pathlib import Path

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.pack.validate import (
    BuildAssertionError,
    assert_build_invariants,
    validate_graph,
)
from framework_reader.schema.entities import (
    Framework,
    FrameworkControl,
    LicenseTier,
)
from framework_reader.schema.sources import SourceRegistry

REGISTRY = SourceRegistry.load(Path("content/allowed_sources.yaml"))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    insert_frameworks(c, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="https://www.nist.gov/cyberframework",
        license_note="public domain",
    )])
    insert_controls(c, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0", parent_id=None,
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE,
    )])
    yield c
    c.close()


def test_clean_graph_has_no_issues(conn):
    assert validate_graph(conn) == []


def test_dangling_mapping_endpoint_is_reported(conn):
    conn.execute(
        "INSERT INTO mapping (from_id, to_id, relation, level, source, source_version) "
        "VALUES (?, ?, 'related', 'L1_OFFICIAL', 'NIST-CPRT-csf-pf-to-sp800-53r5', '2024-02')",
        ("NIST-CSF-2.0:DE.CM-01", "NIST-800-53-R5:DOES-NOT-EXIST"),
    )
    conn.commit()
    kinds = {i.kind for i in validate_graph(conn)}
    assert "dangling_mapping_endpoint" in kinds


def test_dangling_parent_is_reported(conn):
    conn.execute(
        "INSERT INTO framework_control "
        "(id, framework_id, parent_id, label, label_is_original, status) "
        "VALUES (?, 'NIST-CSF-2.0', 'NIST-CSF-2.0:NOPE', 'x', 1, 'active')",
        ("NIST-CSF-2.0:DE.CM-99",),
    )
    conn.commit()
    kinds = {i.kind for i in validate_graph(conn)}
    assert "dangling_parent" in kinds


def test_build_fails_when_original_text_is_not_empty(conn):
    """法律边界：构建产物中原文表必须为空。spec §4.2⑤"""
    conn.execute(
        "INSERT INTO original_text (control_id, locale, body) VALUES (?, 'en', ?)",
        ("ISO-27002-2022:A.8.16", "Networks shall be monitored..."),
    )
    conn.commit()
    with pytest.raises(BuildAssertionError, match="original_text"):
        assert_build_invariants(conn, REGISTRY)


def test_build_fails_on_disallowed_mapping_source(conn):
    conn.execute(
        "INSERT INTO mapping (from_id, to_id, relation, level, source, source_version) "
        "VALUES (?, ?, 'related', 'L2_PUBLIC', 'SCF-2026.1', '2026.1')",
        ("NIST-CSF-2.0:DE.CM-01", "ISO-27001-2022:A.8.16"),
    )
    conn.commit()
    with pytest.raises(BuildAssertionError, match="SCF"):
        assert_build_invariants(conn, REGISTRY)


def test_build_passes_on_clean_db(conn):
    # R13：不传 baseline_path 时跳过 ID 稳定性（夹具只有 1 条控制）
    assert_build_invariants(conn, REGISTRY)  # 不抛异常即通过


def test_build_fails_when_published_id_disappears(tmp_path, conn):
    """ID 稳定性只在传入 baseline_path 时检查。R13 / spec §8②"""
    baseline = tmp_path / "published_control_ids.json"
    baseline.write_text(
        json.dumps(["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:GONE-01"]),
        encoding="utf-8",
    )
    with pytest.raises(BuildAssertionError, match="control_id"):
        assert_build_invariants(conn, REGISTRY, baseline_path=baseline)


def test_build_passes_when_baseline_ids_present(tmp_path, conn):
    baseline = tmp_path / "published_control_ids.json"
    baseline.write_text(json.dumps(["NIST-CSF-2.0:DE.CM-01"]), encoding="utf-8")
    assert_build_invariants(conn, REGISTRY, baseline_path=baseline)
