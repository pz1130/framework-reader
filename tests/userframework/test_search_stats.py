"""搜索命中记账。首页「经常搜索」读这里，不读回忆。"""
from datetime import datetime, timedelta, timezone

from framework_reader.userframework.search_stats import record, top


def test_top_is_empty_when_nobody_has_searched(tmp_path):
    assert top(tmp_path / "user.sqlite") == []


def test_a_hit_shows_up(tmp_path):
    db = tmp_path / "user.sqlite"
    record(db, ["NIST-CSF-2.0:DE.CM-01"])
    assert top(db) == ["NIST-CSF-2.0:DE.CM-01"]


def test_more_hits_rank_higher(tmp_path):
    db = tmp_path / "user.sqlite"
    record(db, ["NIST-CSF-2.0:DE.CM-01"])
    record(db, ["NIST-CSF-2.0:PR.AA-01"])
    record(db, ["NIST-CSF-2.0:PR.AA-01"])
    assert top(db)[0] == "NIST-CSF-2.0:PR.AA-01"


def test_a_search_only_keeps_the_first_few_hits(tmp_path):
    """一次搜出二十条，不能把目录前五个全刷成「经常」。"""
    db = tmp_path / "user.sqlite"
    record(db, [f"NIST-CSF-2.0:X.{i}" for i in range(20)])
    assert len(top(db, limit=20)) == 5


def test_old_hits_fall_out_of_the_window(tmp_path, monkeypatch):
    db = tmp_path / "user.sqlite"
    old = datetime.now(timezone.utc) - timedelta(days=200)
    record(db, ["NIST-CSF-2.0:OLD"], at=old)
    record(db, ["NIST-CSF-2.0:NEW"])
    assert top(db) == ["NIST-CSF-2.0:NEW"]
