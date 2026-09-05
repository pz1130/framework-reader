"""预览态。见 2026-08-25 AI 导入设计 §3

**落盘，不在内存。** 它代表已经花掉的钱——重启丢掉等于让人再花一次。
"""
from framework_reader.userframework.import_draft import ImportDraftStore
from framework_reader.userframework.outline import Problem, Span


def _store(tmp_path):
    return ImportDraftStore(tmp_path / "user.sqlite")


def _spans():
    return [Span(ref="5.1", label="账号管理", parent=None, start=2, end=3)]


def test_a_draft_survives_a_new_store_object(tmp_path):
    """新建一个 store 就是「重启」——进程内状态在这一步会全丢。"""
    draft_id = _store(tmp_path).create(
        framework_id="ACME-1", name="ACME 制度", source_text="a\nb\nc",
        spans=_spans(), problems=[], actor="ann@acme.cn")
    again = _store(tmp_path).load(draft_id)
    assert again is not None
    assert again.framework_id == "ACME-1"
    assert again.name == "ACME 制度"
    assert again.spans[0].ref == "5.1"
    assert (again.spans[0].start, again.spans[0].end) == (2, 3)


def test_the_source_snapshot_comes_back_byte_for_byte(tmp_path):
    """条款正文靠行号从它截。它变了，正文就跟着变了。"""
    text = "五、账号管理\n　　公司应当为每一名员工分配唯一账号。\n离职当日停用。"
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text=text,
                            spans=_spans(), problems=[], actor="x")
    assert store.load(draft_id).source_text == text


def test_the_parent_link_survives_the_round_trip(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(
        framework_id="A", name="n", source_text="a\nb\nc",
        spans=[Span(ref="5.1.1", label="子条款", parent="5.1", start=1, end=2)],
        problems=[], actor="x")
    assert store.load(draft_id).spans[0].parent == "5.1"


def test_problems_come_back_too(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(
        framework_id="A", name="n", source_text="a", spans=[],
        problems=[Problem("uncovered", "原文第 1–9 行没能切出条款。")], actor="x")
    got = store.load(draft_id)
    assert got.problems[0].kind == "uncovered"
    assert "1–9" in got.problems[0].detail


def test_two_drafts_do_not_collide(tmp_path):
    store = _store(tmp_path)
    a = store.create(framework_id="A", name="a", source_text="x",
                     spans=[], problems=[], actor="x")
    b = store.create(framework_id="B", name="b", source_text="y",
                     spans=[], problems=[], actor="x")
    assert a != b
    assert store.load(a).framework_id == "A"
    assert store.load(b).framework_id == "B"


def test_edits_are_saved(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a\nb\nc",
                            spans=_spans(), problems=[], actor="x")
    store.save(draft_id,
               spans=[Span(ref="9.9", label="改过的", parent=None, start=1, end=1)],
               dropped={"0"})
    got = store.load(draft_id)
    assert got.spans[0].ref == "9.9"
    assert got.dropped == {"0"}


def test_saving_does_not_touch_the_snapshot(tmp_path):
    """改编号标题不该动原文——正文还要从它截。"""
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a\nb\nc",
                            spans=_spans(), problems=[], actor="x")
    store.save(draft_id, spans=[], dropped=set())
    assert store.load(draft_id).source_text == "a\nb\nc"


def test_a_fresh_draft_has_nothing_dropped(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a",
                            spans=_spans(), problems=[], actor="x")
    assert store.load(draft_id).dropped == set()


def test_a_deleted_draft_is_gone(tmp_path):
    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a",
                            spans=[], problems=[], actor="x")
    store.delete(draft_id)
    assert store.load(draft_id) is None


def test_loading_something_that_never_existed_is_none_not_a_crash(tmp_path):
    assert _store(tmp_path).load("no-such-draft") is None


def test_deleting_something_that_never_existed_is_not_a_crash(tmp_path):
    _store(tmp_path).delete("no-such-draft")


def test_who_made_it_is_recorded(tmp_path):
    """花的是组织的钱，得知道是谁按的。"""
    import sqlite3

    store = _store(tmp_path)
    draft_id = store.create(framework_id="A", name="n", source_text="a",
                            spans=[], problems=[], actor="ann@acme.cn")
    conn = sqlite3.connect(tmp_path / "user.sqlite")
    row = conn.execute("SELECT created_by FROM import_draft WHERE id = ?",
                       (draft_id,)).fetchone()
    conn.close()
    assert row[0] == "ann@acme.cn"
