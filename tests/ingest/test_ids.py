from framework_reader.ingest.ids import (
    looks_like_80053_control,
    normalize_80053_control_id,
    rewrite_mapping_80053_ids,
)
from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)


def _l1(frm: str, to: str) -> Mapping:
    return Mapping(
        from_id=frm,
        to_id=to,
        relation=Relation.RELATED,
        provenance=Provenance(
            level=ProvenanceLevel.L1_OFFICIAL,
            source="NIST-OLIR-csf-2.0-to-sp800-53r5",
            source_version="2024-02",
        ),
    )


def test_strips_leading_zeros_per_hyphen_segment():
    assert normalize_80053_control_id("AC-01") == "AC-1"
    assert normalize_80053_control_id("SI-04") == "SI-4"


def test_converts_paren_enhancement_to_dotted_oscal_form():
    """OSCAL catalog uses ac-2.12, not AC-02(12)."""
    assert normalize_80053_control_id("AC-02(12)") == "AC-2.12"


def test_already_oscal_shaped_ids_are_stable():
    assert normalize_80053_control_id("AC-1") == "AC-1"
    assert normalize_80053_control_id("AC-2.1") == "AC-2.1"
    assert normalize_80053_control_id("AC-2.12") == "AC-2.12"
    assert normalize_80053_control_id("PM-11") == "PM-11"


def test_does_not_collapse_dot_ten_to_dot_one():
    assert normalize_80053_control_id("AC-2.10") == "AC-2.10"
    assert normalize_80053_control_id("AC-02(10)") == "AC-2.10"


def test_normalizes_prefixed_mapping_endpoint():
    assert (
        normalize_80053_control_id("NIST-800-53-R5:AC-01")
        == "NIST-800-53-R5:AC-1"
    )
    assert (
        normalize_80053_control_id("NIST-800-53-R5:AC-02(12)")
        == "NIST-800-53-R5:AC-2.12"
    )


def test_family_codes_are_not_80053_controls():
    assert looks_like_80053_control("AC-01")
    assert looks_like_80053_control("AC-2.12")
    assert not looks_like_80053_control("CP")
    assert not looks_like_80053_control("IR")
    assert not looks_like_80053_control("PT")


def test_rewrite_normalizes_80053_endpoints_and_drops_families():
    edges = rewrite_mapping_80053_ids([
        _l1("NIST-CSF-2.0:DE.CM-01", "NIST-800-53-R5:SI-04"),
        _l1("NIST-800-53-R5:AC-02(12)", "ISO-27002-2022:A.5.1"),
        _l1("NIST-CSF-2.0:PR.IR-03", "NIST-800-53-R5:CP"),
    ])
    pairs = {(e.from_id, e.to_id) for e in edges}
    assert ("NIST-CSF-2.0:DE.CM-01", "NIST-800-53-R5:SI-4") in pairs
    assert ("NIST-800-53-R5:AC-2.12", "ISO-27002-2022:A.5.1") in pairs
    assert all(not e.to_id.endswith(":CP") for e in edges)
    assert all(e.from_id.startswith("NIST-CSF-2.0:") or
               e.from_id.startswith("NIST-800-53-R5:") for e in edges)
