"""自评历史与复评对比。主表永远只留最新一次，对比靠追加的流水。"""
from framework_reader.assess.store import AssessStore


def test_every_record_appends_a_history_row(tmp_path):
    store = AssessStore(tmp_path / "user.sqlite")
    store.record("ACME-1:4.1", level=1)
    store.record("ACME-1:4.1", level=2)
    rows = store._connect().execute(
        "SELECT level FROM assessment_history WHERE control_id = ? "
        "ORDER BY assessed_at", ("ACME-1:4.1",)).fetchall()
    assert [r["level"] for r in rows] == [1, 2]
    assert store.get("ACME-1:4.1").level == 2


def test_changes_reports_the_flip_and_only_the_flip(tmp_path):
    """1 档 → 2 档是复评成果；当场记错重记一次（1→1）不算变化。"""
    store = AssessStore(tmp_path / "user.sqlite")
    store.record("ACME-1:4.1", level=1)
    store.record("ACME-1:4.1", level=1)   # 手滑又记了一遍，同值折叠
    store.record("ACME-1:4.1", level=2)
    store.record("ACME-1:4.2", level=2)   # 只记过一次，没有「变化」可言
    changes = store.changes()
    assert len(changes) == 1
    assert changes[0]["control_id"] == "ACME-1:4.1"
    assert changes[0]["from"] == "L1" and changes[0]["to"] == "L2"


def test_changes_distinguishes_not_applicable(tmp_path):
    store = AssessStore(tmp_path / "user.sqlite")
    store.record("ACME-1:4.1", level=1)
    store.record("ACME-1:4.1", applicable=False, reason="外包了")
    (change,) = store.changes()
    assert change["from"] == "L1" and change["to"] == "N/A"


def test_scopes_do_not_leak_into_each_other(tmp_path):
    store = AssessStore(tmp_path / "user.sqlite")
    store.record("ACME-1:4.1", level=1, scope="数据中心")
    store.record("ACME-1:4.1", level=2, scope="数据中心")
    store.record("ACME-1:4.1", level=1, scope="default")
    assert store.changes("数据中心")[0]["to"] == "L2"
    assert store.changes("default") == []
