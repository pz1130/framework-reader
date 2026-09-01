from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)


def _prov(level, **kw):
    base = dict(level=level, source="NIST-CPRT-csf-pf-to-sp800-53r5", source_version="2024-02")
    base.update(kw)
    return Provenance(**base)


def test_l1_edge_is_exportable():
    m = Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01",
        to_id="NIST-800-53-R5:SI-4",
        relation=Relation.RELATED,
        provenance=_prov(ProvenanceLevel.L1_OFFICIAL),
        note="",
    )
    assert m.exportable is True


def test_derived_edge_is_not_exportable():
    """L2-推导 边不可直接导出。spec §3.3"""
    m = Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01",
        to_id="ISO-27001-2022:A.8.16",
        relation=Relation.RELATED,
        provenance=_prov(
            ProvenanceLevel.L2_DERIVED,
            source="derived:two-hop",
            derived_via=["NIST-800-53-R5:SI-4"],
        ),
        note="",
    )
    assert m.exportable is False


def test_l4_edge_is_not_exportable():
    m = Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01",
        to_id="PCI-DSS-4.0:10.4.1",
        relation=Relation.RELATED,
        provenance=_prov(ProvenanceLevel.L4_AI, source="ai:claude-opus-5"),
        note="",
    )
    assert m.exportable is False


def test_l3_edge_requires_confirmer_and_timestamp():
    with pytest.raises(ValidationError, match="confirmed_by"):
        _prov(ProvenanceLevel.L3_CONFIRMED)

    p = _prov(
        ProvenanceLevel.L3_CONFIRMED,
        confirmed_by="author",
        confirmed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    assert p.confirmed_by == "author"


def test_derived_level_requires_derived_via():
    with pytest.raises(ValidationError, match="derived_via"):
        _prov(ProvenanceLevel.L2_DERIVED, source="derived:two-hop")


def test_self_loop_is_rejected():
    with pytest.raises(ValidationError, match="from_id"):
        Mapping(
            from_id="NIST-CSF-2.0:DE.CM-01",
            to_id="NIST-CSF-2.0:DE.CM-01",
            relation=Relation.EQUIVALENT,
            provenance=_prov(ProvenanceLevel.L1_OFFICIAL),
            note="",
        )
