from pathlib import Path

from framework_reader.ingest.cprt import parse_cprt_mappings
from framework_reader.schema.mapping import ProvenanceLevel

FIXTURE = Path("tests/fixtures/csf_to_800-53_sample.xlsx")
# sheet 名与表头行号取自 Task 6 观察到的真实结构，见 tests/fixtures/README.md
SHEET = "Relationships"
HEADER_ROW = 1


def test_all_edges_are_l1_official():
    edges = parse_cprt_mappings(FIXTURE, sheet=SHEET, header_row=HEADER_ROW)
    assert edges, "夹具应至少解析出一条边"
    assert all(e.provenance.level is ProvenanceLevel.L1_OFFICIAL for e in edges)
    assert all(e.provenance.source == "NIST-OLIR-csf-2.0-to-sp800-53r5" for e in edges)


def test_edge_endpoints_are_namespaced():
    edges = parse_cprt_mappings(FIXTURE, sheet=SHEET, header_row=HEADER_ROW)
    assert all(e.from_id.startswith("NIST-CSF-2.0:") for e in edges)
    assert all(e.to_id.startswith("NIST-800-53-R5:") for e in edges)


def test_blank_rows_are_skipped():
    edges = parse_cprt_mappings(FIXTURE, sheet=SHEET, header_row=HEADER_ROW)
    assert all(e.from_id.strip() and e.to_id.strip() for e in edges)


def test_no_self_loops_survive():
    edges = parse_cprt_mappings(FIXTURE, sheet=SHEET, header_row=HEADER_ROW)
    assert all(e.from_id != e.to_id for e in edges)
