"""把落错地方的解读搬回用户库。主 spec §7.3.5

b971e12 起草导入的框架时写的是 `content/interpretations/<框架>/`——产品的内容仓。
存储层已经改成按 tier 分流，但**已经写在那儿的**不会自己长脚。这就是搬运工。
"""
import pytest
import yaml

from framework_reader.interpret.migrate import migrate_user_drafts
from framework_reader.interpret.model import ALL_FIELDS, Basis, Field, Interpretation
from framework_reader.interpret.store import InterpretationStore
from framework_reader.interpret.user_store import UserInterpretationStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def imported(home):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-SEC-2026", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "留存不少于六个月。")],
    )
    return UserFrameworkStore()


def _draft(control_id: str, intent: str = "防的是日志被顺手清掉") -> Interpretation:
    return Interpretation(
        control_id=control_id,
        fields={
            name: Field(
                value=(intent if name == "intent"
                       else {"1": "一档", "2": "二档", "3": "三档"} if name == "practice"
                       else "x"),
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
    )


@pytest.fixture
def content_root(tmp_path):
    """`content/interpretations/` 的替身——测试不碰真的内容仓。"""
    return tmp_path / "content" / "interpretations"


def _put(root, control_id: str, intent: str = "防的是日志被顺手清掉"):
    InterpretationStore(root).save(_draft(control_id, intent))


def test_a_stray_draft_lands_in_the_user_library(imported, content_root):
    _put(content_root, "ACME-SEC-2026:4.1")
    migrate_user_drafts(content_root)
    assert UserInterpretationStore().load("ACME-SEC-2026:4.1").fields["intent"].value == (
        "防的是日志被顺手清掉"
    )


def test_the_report_names_what_moved(imported, content_root):
    _put(content_root, "ACME-SEC-2026:4.1")
    report = migrate_user_drafts(content_root)
    assert report.moved == ["ACME-SEC-2026:4.1"]


def test_a_builtin_framework_is_left_alone(imported, content_root):
    """内置框架的解读**就该**在内容仓里——那是我们要发布的东西。"""
    _put(content_root, "NIST-CSF-2.0:DE.CM-01")
    migrate_user_drafts(content_root)
    assert (content_root / "NIST-CSF-2.0" / "DE.CM-01.yaml").exists()
    assert not UserInterpretationStore().exists("NIST-CSF-2.0:DE.CM-01")


def test_the_source_file_is_kept_by_default(imported, content_root):
    """搬运不删原件：原件被 git 追踪，删不删是另一个决定。"""
    _put(content_root, "ACME-SEC-2026:4.1")
    migrate_user_drafts(content_root)
    assert (content_root / "ACME-SEC-2026" / "4.1.yaml").exists()


def test_delete_removes_the_source_only_after_it_landed(imported, content_root):
    _put(content_root, "ACME-SEC-2026:4.1")
    migrate_user_drafts(content_root, delete=True)
    assert not (content_root / "ACME-SEC-2026" / "4.1.yaml").exists()
    assert UserInterpretationStore().exists("ACME-SEC-2026:4.1")


def test_an_already_migrated_draft_is_not_overwritten(imported, content_root):
    """用户库里那份可能是他改过的。默认不覆盖。"""
    UserInterpretationStore().save(_draft("ACME-SEC-2026:4.1", "我自己改过的"))
    _put(content_root, "ACME-SEC-2026:4.1", "YAML 里的旧版")
    report = migrate_user_drafts(content_root)
    assert report.moved == []
    assert UserInterpretationStore().load("ACME-SEC-2026:4.1").fields["intent"].value == (
        "我自己改过的"
    )


def test_force_overwrites_what_is_already_there(imported, content_root):
    UserInterpretationStore().save(_draft("ACME-SEC-2026:4.1", "旧的"))
    _put(content_root, "ACME-SEC-2026:4.1", "YAML 里的")
    migrate_user_drafts(content_root, force=True)
    assert UserInterpretationStore().load("ACME-SEC-2026:4.1").fields["intent"].value == (
        "YAML 里的"
    )


def test_a_draft_whose_control_is_gone_is_reported_not_silently_dropped(
    imported, content_root
):
    """重新导入过表格、编号改了——搬进去也没有页面够得着，但必须说出来。"""
    _put(content_root, "ACME-SEC-2026:9.9")
    report = migrate_user_drafts(content_root)
    assert report.moved == []
    assert report.skipped and report.skipped[0][0] == "ACME-SEC-2026:9.9"


def test_a_corrupt_file_does_not_stop_the_rest(imported, content_root):
    """一份坏文件不能把另外一百份一起拖下水。"""
    _put(content_root, "ACME-SEC-2026:4.1")
    bad = content_root / "ACME-SEC-2026" / "broken.yaml"
    bad.write_text("这不是解读: [", encoding="utf-8")
    report = migrate_user_drafts(content_root)
    assert report.moved == ["ACME-SEC-2026:4.1"]
    assert any("broken.yaml" in what and "could not read" in why
               for what, why in report.skipped)


def test_nothing_to_do_is_not_an_error(imported, content_root):
    report = migrate_user_drafts(content_root)
    assert report.moved == [] and report.skipped == []


def test_a_missing_content_directory_is_not_an_error(imported, tmp_path):
    assert migrate_user_drafts(tmp_path / "nope").moved == []


# ---------- 删掉框架要把解读一起带走 ----------

def test_removing_an_imported_framework_takes_its_interpretations(imported):
    """否则用户库里留下够不着的孤儿行，下次导入同名框架还会串味。"""
    UserInterpretationStore().save(_draft("ACME-SEC-2026:4.1"))
    imported.remove("ACME-SEC-2026")
    assert not UserInterpretationStore().exists("ACME-SEC-2026:4.1")


def test_removing_one_framework_leaves_another_alone(imported):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="OTHER", name="别的", controls=[("1", "条", None, "")],
    )
    UserInterpretationStore().save(_draft("OTHER:1"))
    UserInterpretationStore().save(_draft("ACME-SEC-2026:4.1"))
    imported.remove("ACME-SEC-2026")
    assert UserInterpretationStore().exists("OTHER:1")


def test_the_yaml_on_disk_is_readable_as_written(imported, content_root):
    """搬运读的是 InterpretationStore 写出来的格式，不是我们臆想的格式。"""
    _put(content_root, "ACME-SEC-2026:4.1")
    raw = yaml.safe_load(
        (content_root / "ACME-SEC-2026" / "4.1.yaml").read_text(encoding="utf-8")
    )
    assert raw["control_id"] == "ACME-SEC-2026:4.1"
