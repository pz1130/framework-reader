"""可发布形态：106 条 CSF 中文解读。主 spec §7.3.4"""
import pytest

from framework_reader.publish.site import Entry, collect, render_page


class _Ctl:
    def __init__(self, id, label):
        self.id, self.label, self.status = id, label, "active"


class _Neighbor:
    def __init__(self, cid, label, exportable, level, source):
        self.control_id, self.label = cid, label
        self.exportable, self.level, self.source = exportable, level, source
        self.relation = "related"


class _API:
    def __init__(self, controls, interps, neighbors=()):
        self._c, self._i, self._n = controls, interps, list(neighbors)

    def list_controls(self, framework_id, **kw):
        return self._c

    def interpretation(self, cid, locale="zh-CN"):
        return self._i.get(cid, {})

    def interpretation_state(self, cid, locale="zh-CN"):
        return "draft" if cid in self._i else None

    def neighbors(self, cid, exportable_only=False):
        return [n for n in self._n if n.exportable or not exportable_only]


def _fields(**over):
    base = {
        "intent": "防的是没人看网络", "plain_zh": "把网段盯着",
        "practice": {"1": "有探针", "2": "有清单", "3": "自动化"},
        "evidence": "看板截图", "common_myth": "以为装了 IDS 就够",
        "auditor_asks": ["上次告警什么时候？"], "regional_note": None,
    }
    base.update(over)
    return {k: {"value": v, "basis": "inferred"} for k, v in base.items()}


CTL = _Ctl("NIST-CSF-2.0:DE.CM-01", "Networks are monitored")


def test_only_controls_with_an_interpretation_are_published():
    api = _API([CTL, _Ctl("NIST-CSF-2.0:DE.CM-02", "没解读的")],
               {CTL.id: _fields()})
    assert [e.control_id for e in collect(api)] == [CTL.id]


def test_the_official_english_label_travels_with_it():
    entries = collect(_API([CTL], {CTL.id: _fields()}))
    assert entries[0].label == "Networks are monitored"


def test_empty_fields_are_dropped_not_rendered_as_null():
    entries = collect(_API([CTL], {CTL.id: _fields()}))
    assert "regional_note" not in dict(entries[0].fields)


def test_only_exportable_edges_are_published():
    """L2 推导边 correct 17%，不进任何导出物。主 spec §3.3"""
    api = _API([CTL], {CTL.id: _fields()}, neighbors=[
        _Neighbor("NIST-800-53-R5:SI-4", "System Monitoring", True, "L1_OFFICIAL", "OLIR"),
        _Neighbor("ISO-27002-2022:A.8.16", "活动监控", False, "L2_DERIVED", "derived:two-hop"),
    ])
    edges = collect(api)[0].mappings
    assert [e.control_id for e in edges] == ["NIST-800-53-R5:SI-4"]


def test_the_page_states_the_content_is_an_ai_draft():
    """106 条全是 state=draft，一条都没签过字。不写在脸上就是欺骗读者。"""
    page = render_page(collect(_API([CTL], {CTL.id: _fields()})))
    assert "drafted by AI" in page


def test_the_page_states_that_no_copyrighted_text_is_redistributed():
    page = render_page(collect(_API([CTL], {CTL.id: _fields()})))
    assert "Source text" in page


def test_every_collected_control_appears_in_the_page():
    api = _API([CTL, _Ctl("NIST-CSF-2.0:GV.OC-01", "Mission is understood")],
               {CTL.id: _fields(), "NIST-CSF-2.0:GV.OC-01": _fields()})
    page = render_page(collect(api))
    assert "DE.CM-01" in page and "GV.OC-01" in page


def test_html_special_characters_in_content_do_not_break_the_page():
    api = _API([CTL], {CTL.id: _fields(intent="防的是 <script>alert(1)</script>")})
    page = render_page(collect(api))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_entries_carry_their_group_for_filtering():
    entries = collect(_API([CTL], {CTL.id: _fields()}))
    assert entries[0].group == "DE"


def test_the_page_is_self_contained():
    """要能扔到任何静态托管上，不许外链 CDN。"""
    page = render_page(collect(_API([CTL], {CTL.id: _fields()})))
    for forbidden in ("http://", "cdn.", "<script src="):
        assert forbidden not in page


def test_the_page_declares_utf8_so_it_survives_plain_static_hosting():
    """不声明 charset 的话，随便扔到一个静态服务器上中文就是乱码。

    要求是「声明得够早」，不是「在第一个字节」——HTML 规范给的窗口是前 1024 字节，
    而第一行现在是 `<!doctype html>`（见 tests/web/test_doctype.py）。
    """
    from framework_reader.publish.site import collect, render_page

    page = render_page(collect(_API([CTL], {CTL.id: _fields()})))
    head = page[:1024]
    assert '<meta charset="utf-8">' in head, head[:80]


def test_controls_follow_the_framework_order_not_the_alphabet():
    """CSF 的顺序是 GV→ID→PR→DE→RS→RC。按字母排会让 DE 跑到最前面。"""
    from framework_reader.publish.site import collect

    ids = ["NIST-CSF-2.0:DE.AE-02", "NIST-CSF-2.0:GV.OC-01", "NIST-CSF-2.0:RC.RP-01"]
    api = _API([_Ctl(i, "x") for i in ids], {i: _fields() for i in ids})
    assert [e.group for e in collect(api)] == ["GV", "DE", "RC"]


# ---------- 多框架 ----------

ISO = _Ctl("ISO-27002-2022:A.5.1", "信息安全方针")


def test_iso_groups_by_theme_not_by_first_letter():
    """A.5.1 按点切第一段是「A」——93 条会挤成一组。ISO 的分组是主题 A.5。"""
    from framework_reader.publish.site import collect

    entries = collect(_API([ISO], {ISO.id: _fields()}), "ISO-27002-2022")
    assert entries[0].group == "A.5"


def test_csf_still_groups_by_function():
    from framework_reader.publish.site import collect

    assert collect(_API([CTL], {CTL.id: _fields()}))[0].group == "DE"


def test_entries_know_which_framework_they_belong_to():
    from framework_reader.publish.site import collect

    assert collect(_API([ISO], {ISO.id: _fields()}), "ISO-27002-2022")[0].framework == (
        "ISO-27002-2022"
    )


def test_a_two_framework_page_offers_both_as_tabs():
    from framework_reader.publish.site import collect, render_multi

    page = render_multi([
        ("NIST-CSF-2.0", collect(_API([CTL], {CTL.id: _fields()}))),
        ("ISO-27002-2022", collect(_API([ISO], {ISO.id: _fields()}), "ISO-27002-2022")),
    ])
    assert "NIST CSF 2.0" in page and "ISO/IEC 27002:2022" in page
    assert 'data-fw="ISO-27002-2022"' in page


def test_the_iso_framework_states_that_its_text_is_not_reproduced():
    """ISO 是必须购买的标准。页面上一个字的原文都没有，这件事要写出来。"""
    from framework_reader.publish.site import collect, render_multi

    page = render_multi([
        ("ISO-27002-2022", collect(_API([ISO], {ISO.id: _fields()}), "ISO-27002-2022")),
    ])
    assert "purchased" in page


def test_both_frameworks_controls_appear():
    from framework_reader.publish.site import collect, render_multi

    page = render_multi([
        ("NIST-CSF-2.0", collect(_API([CTL], {CTL.id: _fields()}))),
        ("ISO-27002-2022", collect(_API([ISO], {ISO.id: _fields()}), "ISO-27002-2022")),
    ])
    assert "DE.CM-01" in page and "A.5.1" in page


def test_hidden_beats_the_display_rules():
    """.chips 是 flex，hidden 的 display:none 会被它盖掉——两组筛选会同时显示。"""
    from framework_reader.publish.template import PAGE

    assert "[hidden]{display:none !important}" in PAGE
