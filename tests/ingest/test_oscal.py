from pathlib import Path

from framework_reader.ingest.oscal import parse_oscal_catalog, unified_controls_from_csf
from framework_reader.schema.entities import ControlStatus, LicenseTier

FIXTURE = Path("tests/fixtures/oscal_800-53r5_sample.json")
CSF_FIXTURE = Path("tests/fixtures/csf_2.0_oscal_sample.json")


def test_returns_framework_with_public_domain_tier():
    fw, _ = parse_oscal_catalog(FIXTURE, framework_id="NIST-800-53-R5")
    assert fw.id == "NIST-800-53-R5"
    assert fw.tier is LicenseTier.A_EMBEDDABLE


def test_control_ids_are_namespaced_and_unique():
    _, controls = parse_oscal_catalog(FIXTURE, framework_id="NIST-800-53-R5")
    assert controls, "夹具应至少解析出一条控制"
    ids = [c.id for c in controls]
    assert len(ids) == len(set(ids)), "control_id 必须唯一"
    assert all(cid.startswith("NIST-800-53-R5:") for cid in ids)


def test_original_titles_are_allowed_for_tier_a():
    _, controls = parse_oscal_catalog(FIXTURE, framework_id="NIST-800-53-R5")
    # 800-53 是公共领域，可直接使用官方标题
    assert all(c.label_is_original for c in controls)
    assert all(c.label.strip() for c in controls)


def test_nested_enhancements_link_to_parent():
    _, controls = parse_oscal_catalog(FIXTURE, framework_id="NIST-800-53-R5")
    by_id = {c.id: c for c in controls}
    children = [c for c in controls if c.parent_id is not None]
    assert children, "夹具应含嵌套 enhancement，children 不得为空"
    for child in children:
        assert child.parent_id in by_id, f"{child.id} 的 parent_id 悬空"


def test_withdrawn_80053_enhancement_is_deprecated():
    _, controls = parse_oscal_catalog(FIXTURE, framework_id="NIST-800-53-R5")
    by_id = {c.id: c for c in controls}
    assert by_id["NIST-800-53-R5:AC-2.10"].status is ControlStatus.DEPRECATED
    assert by_id["NIST-800-53-R5:AC-2.12"].status is ControlStatus.ACTIVE


def test_csf_catalog_emits_namespaced_controls():
    fw, controls = parse_oscal_catalog(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    assert fw.id == "NIST-CSF-2.0"
    assert fw.tier is LicenseTier.A_EMBEDDABLE
    ids = [c.id for c in controls]
    assert "NIST-CSF-2.0:DE.CM-01" in ids
    assert all(cid.startswith("NIST-CSF-2.0:") for cid in ids)


def test_csf_subcategory_uses_statement_when_title_is_id():
    _, controls = parse_oscal_catalog(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    by_id = {c.id: c for c in controls}
    cm01 = by_id["NIST-CSF-2.0:DE.CM-01"]
    assert cm01.label == (
        "Networks and network services are monitored to find potentially adverse events"
    )
    assert cm01.label_is_original is True
    assert cm01.status is ControlStatus.ACTIVE
    # CSF subcategory numbers keep the two-digit form; do not strip to DE.CM-1
    assert cm01.id == "NIST-CSF-2.0:DE.CM-01"


def test_csf_withdrawn_subcategory_is_deprecated():
    _, controls = parse_oscal_catalog(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    by_id = {c.id: c for c in controls}
    assert by_id["NIST-CSF-2.0:DE.AE-01"].status is ControlStatus.DEPRECATED


def test_unified_controls_from_active_csf_subcategories_only():
    _, controls = parse_oscal_catalog(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    unified = unified_controls_from_csf(controls)
    ids = {u.id for u in unified}
    assert "UC:DE.CM-01" in ids
    assert "UC:DE.AE-01" not in ids
    assert all(u.locale == "zh-CN" for u in unified)
    assert all(u.id.startswith("UC:") for u in unified)
    assert "UC:DE.CM" not in ids
    by_uc = {u.id: u for u in unified}
    assert by_uc["UC:DE.CM-01"].label == (
        "Networks and network services are monitored to find potentially adverse events"
    )
