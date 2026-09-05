"""用户层：自评数据。主 spec §6.1（内容层只读 / 用户层可写，物理分离）"""
import pytest

from framework_reader.assess.store import Assessment, AssessStore


@pytest.fixture
def store(tmp_path):
    return AssessStore(tmp_path / "user.sqlite")


def test_the_database_is_created_on_first_use(tmp_path):
    path = tmp_path / "user.sqlite"
    AssessStore(path).all()
    assert path.exists()


def test_a_level_round_trips(store):
    store.record("NIST-CSF-2.0:DE.CM-01", level=1, note="只有边界有探针")
    got = store.get("NIST-CSF-2.0:DE.CM-01")
    assert got.level == 1
    assert got.note == "只有边界有探针"


def test_re_recording_overwrites_rather_than_duplicates(store):
    store.record("NIST-CSF-2.0:DE.CM-01", level=1)
    store.record("NIST-CSF-2.0:DE.CM-01", level=2, note="补了主机侧")
    assert len(store.all()) == 1
    assert store.get("NIST-CSF-2.0:DE.CM-01").level == 2


def test_zero_means_not_done_and_is_not_confused_with_unassessed(store):
    """practice 只写了 1/2/3 档。没有 0，大量「压根没做」会被迫虚报成 1 档。"""
    store.record("NIST-CSF-2.0:DE.CM-01", level=0)
    assert store.get("NIST-CSF-2.0:DE.CM-01").level == 0
    assert store.get("NIST-CSF-2.0:DE.CM-02") is None


def test_scopes_are_independent(store):
    store.record("NIST-CSF-2.0:DE.CM-01", level=1, scope="总部")
    store.record("NIST-CSF-2.0:DE.CM-01", level=3, scope="研发中心")
    assert store.get("NIST-CSF-2.0:DE.CM-01", scope="总部").level == 1
    assert store.get("NIST-CSF-2.0:DE.CM-01", scope="研发中心").level == 3
    assert len(store.all(scope="总部")) == 1


def test_soa_mode_records_applicability_and_status(store):
    """ISO 的适用性声明要的是：适用吗、做了吗、证据在哪。它没有成熟度档。"""
    store.record(
        "ISO-27002-2022:A.7.4", applicable=False, reason="无自有办公场所，物理监控由业主负责"
    )
    got = store.get("ISO-27002-2022:A.7.4")
    assert got.applicable is False
    assert got.reason.startswith("无自有办公场所")
    assert got.level is None


def test_applicable_defaults_to_true(store):
    store.record("ISO-27002-2022:A.5.1", status="已实施", note="见 SEC-POL-001")
    got = store.get("ISO-27002-2022:A.5.1")
    assert got.applicable is True
    assert got.status == "已实施"


def test_assessed_at_is_recorded(store):
    store.record("NIST-CSF-2.0:DE.CM-01", level=1)
    assert store.get("NIST-CSF-2.0:DE.CM-01").assessed_at.tzinfo is not None


def test_all_is_sorted_by_control_id(store):
    for cid in ("NIST-CSF-2.0:DE.CM-03", "NIST-CSF-2.0:DE.AE-02"):
        store.record(cid, level=1)
    assert [a.control_id for a in store.all()] == [
        "NIST-CSF-2.0:DE.AE-02", "NIST-CSF-2.0:DE.CM-03",
    ]


def test_the_user_database_is_never_the_content_pack(tmp_path):
    """内容包重建（make clean 会 rm -rf build/）不得碰用户数据。§6.1"""
    import inspect

    from framework_reader.assess import store as module

    assert "build/" not in inspect.getsource(module)


def test_assessment_lives_in_the_user_schema():
    from pathlib import Path

    sql = Path("src/framework_reader/pack/user_schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS assessment" in sql
