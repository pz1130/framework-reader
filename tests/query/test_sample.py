import csv
import sqlite3
from pathlib import Path

import pytest

from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_mappings,
)
from framework_reader.query.sample import sample_derived_edges, write_review_sheet
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier
from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)
from framework_reader.schema.sources import SourceRegistry

REGISTRY = SourceRegistry.load(Path("content/allowed_sources.yaml"))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id="NIST-CSF-2.0", name="CSF", version="2.0",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="ISO-27002-2022", name="ISO", version="2022",
                  tier=LicenseTier.C_PURCHASE, source_url="u", license_note="买"),
    ])
    controls, edges = [], []
    for i in range(10):
        controls.append(FrameworkControl(
            id=f"NIST-CSF-2.0:DE.CM-{i:02d}", framework_id="NIST-CSF-2.0",
            label=f"csf label {i}", label_is_original=True,
            framework_tier=LicenseTier.A_EMBEDDABLE))
        controls.append(FrameworkControl(
            id=f"ISO-27002-2022:A.8.{i}", framework_id="ISO-27002-2022",
            label=f"iso 标签 {i}", label_is_original=False,
            framework_tier=LicenseTier.C_PURCHASE))
        edges.append(Mapping(
            from_id=f"NIST-CSF-2.0:DE.CM-{i:02d}", to_id=f"ISO-27002-2022:A.8.{i}",
            relation=Relation.RELATED,
            provenance=Provenance(level=ProvenanceLevel.L2_DERIVED,
                                  source="derived:two-hop", source_version="1",
                                  derived_via=[f"NIST-800-53-R5:SI-{i}"]),
            note=""))
    insert_controls(conn, controls)
    insert_mappings(conn, edges, REGISTRY)
    conn.close()
    return path


def test_sampling_is_deterministic_for_a_seed(db):
    a = sample_derived_edges(db, n=5, seed=42)
    b = sample_derived_edges(db, n=5, seed=42)
    assert [x.from_id for x in a] == [x.from_id for x in b]


def test_sample_only_returns_derived_edges(db):
    samples = sample_derived_edges(db, n=5, seed=1)
    assert len(samples) == 5
    assert all(s.via for s in samples), "每条样本必须带中间节点，便于人工判断推导链"


def test_sample_carries_both_labels(db):
    s = sample_derived_edges(db, n=1, seed=7)[0]
    assert s.from_label and s.to_label


def test_n_larger_than_population_returns_all(db):
    assert len(sample_derived_edges(db, n=100, seed=3)) == 10


def test_review_sheet_has_empty_verdict_column(tmp_path, db):
    out = write_review_sheet(sample_derived_edges(db, n=3, seed=5), tmp_path / "r7.csv")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 3
    assert set(rows[0]) == {
        "from_id", "from_label", "to_id", "to_label", "via", "verdict", "comment"
    }
    assert all(r["verdict"] == "" for r in rows), "判定列必须留空，由人填"
