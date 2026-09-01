"""适用性声明（SoA）。主 spec §7.3.3 第 2 步"""
from datetime import datetime, timezone

from framework_reader.assess.soa import build_soa, render_soa_csv, render_soa_markdown
from framework_reader.assess.store import Assessment

CONTROLS = [
    ("ISO-27002-2022:A.5.1", "信息安全方针"),
    ("ISO-27002-2022:A.5.2", "信息安全角色与职责"),
    ("ISO-27002-2022:A.7.4", "物理安全监控"),
]


def _a(cid, **kw):
    return Assessment(control_id=cid, assessed_at=datetime.now(timezone.utc), **kw)


def test_every_control_appears_even_when_unassessed():
    """SoA 必须覆盖全部 93 条。悄悄漏掉没填的，是把风险藏起来。"""
    rows = build_soa(CONTROLS, [_a(CONTROLS[0][0], status="已实施")])
    assert [r.control_id for r in rows] == [c[0] for c in CONTROLS]


def test_an_unassessed_control_is_marked_as_pending_not_applicable():
    rows = build_soa(CONTROLS, [])
    assert rows[0].applicable is None
    assert rows[0].status == ""


def test_not_applicable_carries_its_reason():
    rows = build_soa(CONTROLS, [_a("ISO-27002-2022:A.7.4", applicable=False, reason="无自有场所")])
    row = [r for r in rows if r.control_id.endswith("A.7.4")][0]
    assert row.applicable is False
    assert row.reason == "无自有场所"


def test_markdown_has_one_row_per_control_plus_header():
    text = render_soa_markdown(build_soa(CONTROLS, []))
    assert text.count("\n|") >= len(CONTROLS)
    assert "Applicability" in text and "Reason if N/A" in text


def test_markdown_marks_pending_rows_visibly():
    text = render_soa_markdown(build_soa(CONTROLS, []))
    assert "TBD" in text


def test_markdown_never_leaks_derived_edges():
    """推导边只能当填写提示，绝不进交付物。§3.3、§7.3.3"""
    rows = build_soa(CONTROLS, [_a(CONTROLS[0][0], status="已实施", note="见 SEC-POL-001")])
    text = render_soa_markdown(rows)
    for word in ("推导", "L2", "线索", "NIST-CSF"):
        assert word not in text


def test_csv_is_parseable_and_keeps_commas_in_notes():
    import csv
    import io

    rows = build_soa(CONTROLS, [_a(CONTROLS[0][0], status="已实施", note="见 A,B 两份制度")])
    parsed = list(csv.reader(io.StringIO(render_soa_csv(rows))))
    assert parsed[0][0] == "Control"
    assert parsed[1][-1] == "见 A,B 两份制度"


def test_a_pipe_in_a_note_does_not_break_the_markdown_table():
    rows = build_soa(CONTROLS, [_a(CONTROLS[0][0], status="已实施", note="A|B")])
    line = [ln for ln in render_soa_markdown(rows).split("\n") if "A.5.1" in ln][0]
    assert line.count("|") == 8


# ---------- 录入时的填写提示 ----------

class _FakeNeighbor:
    def __init__(self, control_id, label, exportable, level):
        self.control_id, self.label = control_id, label
        self.exportable, self.level = exportable, level


class _FakeAPI:
    def __init__(self, neighbors):
        self._n = neighbors

    def neighbors(self, control_id):
        return self._n


def test_official_edges_come_first_and_say_they_are_official():
    from framework_reader.assess.soa import fill_hints

    api = _FakeAPI([
        _FakeNeighbor("X:1", "推导来的", False, "L2_DERIVED"),
        _FakeNeighbor("Y:2", "官方来的", True, "L1_OFFICIAL"),
    ])
    hints = fill_hints(api, "ISO-27002-2022:A.5.1")
    assert "official mapping" in hints[0] and "Y:2" in hints[0]
    assert "not citable" in hints[1]


def test_repeated_labels_are_collapsed():
    """800-53 每族都有一条 `-1 Policy and Procedures`，四条提示会全是同一句。"""
    from framework_reader.assess.soa import fill_hints

    api = _FakeAPI([
        _FakeNeighbor(f"NIST-800-53-R5:{fam}-1", "Policy and Procedures", True, "L1_OFFICIAL")
        for fam in ("AC", "AT", "AU", "CA")
    ] + [_FakeNeighbor("NIST-800-53-R5:SI-4", "System Monitoring", True, "L1_OFFICIAL")])
    hints = fill_hints(api, "ISO-27002-2022:A.5.1")
    assert len(hints) == 2
    assert "System Monitoring" in hints[1]


def test_hints_are_capped():
    from framework_reader.assess.soa import fill_hints

    api = _FakeAPI([
        _FakeNeighbor(f"X:{i}", f"标题 {i}", True, "L1_OFFICIAL") for i in range(20)
    ])
    assert len(fill_hints(api, "c")) <= 4
