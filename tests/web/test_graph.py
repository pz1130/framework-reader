"""Tests for cross-framework relationship graph view and data API."""
import re
import sqlite3
import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks,
)
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)

    insert_frameworks(conn, [
        Framework(id="NIST-CSF-2.0", name="NIST Cybersecurity Framework 2.0", version="2.0",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="ISO-27002-2022", name="ISO/IEC 27002:2022", version="2022",
                  tier=LicenseTier.B_NO_REDIST, source_url="u", license_note="pd"),
    ])

    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:PR.AC-01", framework_id="NIST-CSF-2.0",
                         label="Identities are managed", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="ISO-27002-2022:A.5.1", framework_id="ISO-27002-2022",
                         label="Policies for information security", label_is_original=False,
                         framework_tier=LicenseTier.B_NO_REDIST),
    ])

    conn.execute(
        "INSERT INTO mapping (from_id, to_id, relation, level, source, source_version) "
        "VALUES ('ISO-27002-2022:A.5.1', 'NIST-CSF-2.0:DE.CM-01', 'subset', 'direct', 'test', '1.0')"
    )
    conn.execute(
        "INSERT INTO mapping (from_id, to_id, relation, level, source, source_version) "
        "VALUES ('ISO-27002-2022:A.5.1', 'NIST-CSF-2.0:PR.AC-01', 'superset', 'direct', 'test', '1.0')"
    )
    conn.commit()
    conn.close()

    from framework_reader.web.app import create_app

    return TestClient(create_app(db))


def test_graph_page_renders_ok(client):
    res = client.get("/graph")
    assert res.status_code == 200
    html = res.text
    assert "Relationship Graph" in html
    assert 'id="graph-canvas"' in html
    assert 'href="/graph"' in html
    assert 'id="spacing-slider"' in html
    assert 'id="graph-drawer"' in html
    # Check that frameworks are embedded
    assert "NIST-CSF-2.0" in html
    assert "ISO-27002-2022" in html


def test_api_graph_data(client):
    res = client.get("/api/graph-data")
    assert res.status_code == 200
    data = res.json()
    assert "frameworks" in data
    assert "nodes" in data
    assert "links" in data
    assert len(data["frameworks"]) == 2
    assert len(data["nodes"]) == 3
    assert len(data["links"]) == 2

    # Verify degrees
    node_by_id = {n["id"]: n for n in data["nodes"]}
    assert node_by_id["ISO-27002-2022:A.5.1"]["degree"] == 2
    assert node_by_id["NIST-CSF-2.0:DE.CM-01"]["degree"] == 1


def test_api_graph_data_filter(client):
    res = client.get("/api/graph-data?framework_id=NIST-CSF-2.0")
    assert res.status_code == 200
    data = res.json()
    assert len(data["frameworks"]) == 1
    # Links only between allowed frameworks
    assert len(data["links"]) == 0


def test_graph_page_pulls_nothing_from_network(client):
    """Invariant: no external scripts or remote CDN references."""
    page = client.get("/graph").text
    scripts = "".join(re.findall(r"<script>(.*?)</script>", page, re.S))
    assert "http://" not in scripts and "https://" not in scripts
    assert "<script src" not in page
