import pytest

from framework_reader.ingest.derive import derive_two_hop
from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)


def _l1(a: str, b: str) -> Mapping:
    return Mapping(
        from_id=a, to_id=b, relation=Relation.RELATED,
        provenance=Provenance(
            level=ProvenanceLevel.L1_OFFICIAL, source="NIST-CPRT-csf-pf-to-sp800-53r5",
            source_version="2024-02",
        ),
        note="",
    )


CSF, C53, ISO = "NIST-CSF-2.0:", "NIST-800-53-R5:", "ISO-27001-2022:"


def test_two_hop_produces_derived_edge():
    edges = [_l1(f"{CSF}DE.CM-01", f"{C53}SI-4"), _l1(f"{C53}SI-4", f"{ISO}A.8.16")]
    out = derive_two_hop(edges, via_prefix=C53, from_prefix=CSF, to_prefix=ISO)
    assert len(out) == 1
    e = out[0]
    assert e.from_id == f"{CSF}DE.CM-01"
    assert e.to_id == f"{ISO}A.8.16"
    assert e.provenance.level is ProvenanceLevel.L2_DERIVED
    assert e.provenance.source == "derived:two-hop"
    assert e.provenance.derived_via == [f"{C53}SI-4"]
    assert e.exportable is False


def test_derived_edges_are_deduplicated_and_record_all_paths():
    edges = [
        _l1(f"{CSF}DE.CM-01", f"{C53}SI-4"),
        _l1(f"{CSF}DE.CM-01", f"{C53}AU-6"),
        _l1(f"{C53}SI-4", f"{ISO}A.8.16"),
        _l1(f"{C53}AU-6", f"{ISO}A.8.16"),
    ]
    out = derive_two_hop(edges, via_prefix=C53, from_prefix=CSF, to_prefix=ISO)
    assert len(out) == 1, "同一对端点只产出一条边"
    assert sorted(out[0].provenance.derived_via) == [f"{C53}AU-6", f"{C53}SI-4"]


def test_non_l1_input_edges_are_ignored():
    """只有 L1 边可以参与推导——推导 AI 边会把不确定性放大。"""
    ai = Mapping(
        from_id=f"{CSF}DE.CM-01", to_id=f"{C53}SI-4", relation=Relation.RELATED,
        provenance=Provenance(
            level=ProvenanceLevel.L4_AI, source="ai:claude-opus-5", source_version="1"
        ),
        note="",
    )
    edges = [ai, _l1(f"{C53}SI-4", f"{ISO}A.8.16")]
    assert derive_two_hop(edges, via_prefix=C53, from_prefix=CSF, to_prefix=ISO) == []


def test_no_self_loop_produced():
    edges = [_l1(f"{CSF}A", f"{C53}X"), _l1(f"{C53}X", f"{CSF}A")]
    out = derive_two_hop(edges, via_prefix=C53, from_prefix=CSF, to_prefix=CSF)
    assert out == []
