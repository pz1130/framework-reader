"""差距报告：最弱的先出现，每条给出下一步和证据。主 spec §7.3.3"""
from datetime import datetime, timezone

from framework_reader.assess.report import GapItem, build_gap, render_gap
from framework_reader.assess.store import Assessment

PRACTICE = {"1": "有基础监控", "2": "覆盖面有清单", "3": "自动化并联动变更"}


def _a(cid, level, note=""):
    return Assessment(
        control_id=cid, level=level, note=note, assessed_at=datetime.now(timezone.utc)
    )


def _content(cid):
    return {
        "label": f"{cid} 的标题",
        "practice": PRACTICE,
        "evidence": "监控看板截图、告警处置单",
    }


CONTENT = {f"NIST-CSF-2.0:DE.CM-0{i}": _content(f"NIST-CSF-2.0:DE.CM-0{i}") for i in range(1, 5)}


def test_next_step_is_the_rung_above_the_current_level():
    """「下一步做什么」不需要任何推理，就是查 practice 的下一档。"""
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 1)], CONTENT, total=4)
    assert report.items[0].next_step == "覆盖面有清单"


def test_a_zero_gets_the_first_rung():
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 0)], CONTENT, total=4)
    assert report.items[0].next_step == "有基础监控"


def test_a_control_at_the_top_has_no_next_step_and_drops_out():
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 3)], CONTENT, total=4)
    assert report.items == []
    assert report.at_top == 1


def test_the_weakest_come_first():
    entries = [
        _a("NIST-CSF-2.0:DE.CM-01", 2),
        _a("NIST-CSF-2.0:DE.CM-02", 0),
        _a("NIST-CSF-2.0:DE.CM-03", 1),
    ]
    report = build_gap(entries, CONTENT, total=4)
    assert [i.level for i in report.items] == [0, 1, 2]


def test_coverage_counts_unassessed_controls():
    """已评 1 条不等于全域 1 条。分母是框架的条数，不是已评的条数。"""
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 1)], CONTENT, total=4)
    assert (report.assessed, report.total) == (1, 4)


def test_not_applicable_controls_are_left_out_of_the_gap():
    entry = Assessment(
        control_id="NIST-CSF-2.0:DE.CM-01", applicable=False, reason="无工控网络",
        assessed_at=datetime.now(timezone.utc),
    )
    report = build_gap([entry], CONTENT, total=4)
    assert report.items == []
    assert report.not_applicable == 1


def test_evidence_and_note_travel_with_the_item():
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 1, "只有边界有探针")], CONTENT, total=4)
    item = report.items[0]
    assert item.evidence == "监控看板截图、告警处置单"
    assert item.note == "只有边界有探针"


def test_a_control_without_an_interpretation_still_appears_without_a_next_step():
    """ISO 那 93 条没有 practice——不能因此从报告里消失。"""
    report = build_gap([_a("ISO-27002-2022:A.5.1", 0)], {}, total=93)
    assert report.items[0].next_step == ""


def test_render_puts_the_level_and_the_next_step_in_the_text():
    report = build_gap([_a("NIST-CSF-2.0:DE.CM-01", 1, "只有边界有探针")], CONTENT, total=4)
    text = render_gap(report)
    assert "1/4" in text
    assert "覆盖面有清单" in text
    assert "只有边界有探针" in text


def test_render_says_so_when_nothing_has_been_assessed():
    assert "No self-assessment yet" in render_gap(build_gap([], CONTENT, total=4))
