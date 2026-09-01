"""整改台账的读写。差距报告回答「下一步做什么」，这里记「谁、什么时候、做了没有」。"""
from framework_reader.assess.remediation import RemediationStore


def test_start_creates_a_todo_row(tmp_path):
    store = RemediationStore(tmp_path / "user.sqlite")
    assert store.start("ACME-1:4.1")
    row = store.get("ACME-1:4.1")
    assert row.state == "todo" and row.owner == "" and row.due == ""


def test_start_is_idempotent(tmp_path):
    """差距报告的「立项」按钮可能被点两次；已立项的不许把人填的东西冲掉。"""
    store = RemediationStore(tmp_path / "user.sqlite")
    store.start("ACME-1:4.1")
    store.update("ACME-1:4.1", owner="老张", due="2026-09-30")
    assert not store.start("ACME-1:4.1")
    row = store.get("ACME-1:4.1")
    assert row.owner == "老张" and row.due == "2026-09-30"


def test_update_only_touches_given_fields(tmp_path):
    store = RemediationStore(tmp_path / "user.sqlite")
    store.start("ACME-1:4.1")
    store.update("ACME-1:4.1", owner="老张")
    store.update("ACME-1:4.1", state="doing")
    row = store.get("ACME-1:4.1")
    # 没给的字段原样留着——None 是「没给」，不是「清空」。
    assert row.owner == "老张" and row.state == "doing"


def test_update_rejects_an_unknown_state(tmp_path):
    store = RemediationStore(tmp_path / "user.sqlite")
    store.start("ACME-1:4.1")
    try:
        store.update("ACME-1:4.1", state="搞定了")
    except ValueError:
        pass
    else:
        raise AssertionError("乱填的状态得挡在库门口")


def test_update_on_untracked_row_is_none_not_crash(tmp_path):
    store = RemediationStore(tmp_path / "user.sqlite")
    assert store.update("ACME-1:4.1", state="doing") is None


def test_rows_with_a_due_date_come_first(tmp_path):
    """有期限的紧的先做；没期限的按条款号排在其后。"""
    store = RemediationStore(tmp_path / "user.sqlite")
    for cid in ("ACME-1:4.3", "ACME-1:4.1", "ACME-1:4.2"):
        store.start(cid)
    store.update("ACME-1:4.2", due="2026-09-01")
    store.update("ACME-1:4.1", due="2026-09-15")
    assert [r.control_id for r in store.all()] == [
        "ACME-1:4.2", "ACME-1:4.1", "ACME-1:4.3"]


def test_remove(tmp_path):
    store = RemediationStore(tmp_path / "user.sqlite")
    store.start("ACME-1:4.1")
    assert store.remove("ACME-1:4.1")
    assert store.get("ACME-1:4.1") is None
    assert not store.remove("ACME-1:4.1")
