"""废止控制的去向。spec §8② —— 删除的控制标 deprecated 不删行，并记录取代关系。"""
from pathlib import Path

from framework_reader.ingest.oscal import parse_oscal_catalog, parse_supersessions
from framework_reader.schema.entities import SupersedeRelation

CSF_FIXTURE = Path("tests/fixtures/csf_2.0_oscal_sample.json")
C53_FIXTURE = Path("tests/fixtures/oscal_800-53r5_sample.json")


def _pairs(path: Path, framework_id: str) -> dict[tuple[str, str], SupersedeRelation]:
    return {
        (l.old_id, l.new_id): l.relation
        for l in parse_supersessions(path, framework_id=framework_id)
    }


def test_csf_moved_subcategory_records_its_new_number():
    pairs = _pairs(CSF_FIXTURE, "NIST-CSF-2.0")
    assert pairs["NIST-CSF-2.0:DE.AE-05", "NIST-CSF-2.0:DE.AE-08"] is (
        SupersedeRelation.MOVED_TO
    )


def test_one_withdrawn_control_can_have_several_successors():
    """DE.CM-04 被拆进 DE.CM-01 与 DE.CM-09——单值字段装不下，必须是多对多。"""
    links = parse_supersessions(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    successors = {l.new_id for l in links if l.old_id == "NIST-CSF-2.0:DE.CM-04"}
    assert successors == {"NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-09"}
    assert all(
        l.relation is SupersedeRelation.INCORPORATED_INTO
        for l in links
        if l.old_id == "NIST-CSF-2.0:DE.CM-04"
    )


def test_active_controls_produce_no_links():
    links = parse_supersessions(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    assert all(l.old_id != "NIST-CSF-2.0:DE.CM-01" for l in links)


def test_80053_hyphenated_rel_and_statement_href_resolve_to_the_control():
    """800-53 写 `incorporated-into`（连字符），去向写作 `#ac-2_smt.k`（语句片段）。

    两处都要归一：落到 AC-2 这条控制上，而不是丢掉或留一个悬空片段 ID。
    """
    pairs = _pairs(C53_FIXTURE, "NIST-800-53-R5")
    assert pairs["NIST-800-53-R5:AC-2.10", "NIST-800-53-R5:AC-2"] is (
        SupersedeRelation.INCORPORATED_INTO
    )


def test_80053_hyphenated_moved_to_is_recognised():
    pairs = _pairs(C53_FIXTURE, "NIST-800-53-R5")
    assert pairs["NIST-800-53-R5:AT-3.4", "NIST-800-53-R5:AT-2.4"] is (
        SupersedeRelation.MOVED_TO
    )


def test_link_to_something_that_is_not_a_control_is_dropped():
    """href 可能指向 function/family（ID.GV → GV），或指向本 catalog 之外的条目。

    这些都没有可落地的端点，必须丢掉，不能在图里留悬空引用。
    """
    links = parse_supersessions(CSF_FIXTURE, framework_id="NIST-CSF-2.0")
    assert all(l.old_id != "NIST-CSF-2.0:DE.DP-01" for l in links), (
        "GV.RR-02 不在本夹具内，这条链接应被丢弃"
    )


def test_endpoints_are_always_known_controls():
    for fixture, fid in ((CSF_FIXTURE, "NIST-CSF-2.0"), (C53_FIXTURE, "NIST-800-53-R5")):
        _, controls = parse_oscal_catalog(fixture, framework_id=fid)
        known = {c.id for c in controls}
        links = parse_supersessions(fixture, framework_id=fid)
        assert links, "夹具应至少解析出一条取代关系"
        for link in links:
            assert link.old_id in known, f"{link.old_id} 不是已知控制"
            assert link.new_id in known, f"{link.new_id} 不是已知控制"
            assert link.old_id != link.new_id
