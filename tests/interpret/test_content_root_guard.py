"""内容仓那道门。主 spec §7.3.5

`content/interpretations/` 是我们要发布的内容：进 git、要评审、被 `make build`
烘进内容包。用户导入的框架一个字都不许进——b971e12 就是这么破的。

起草那条路已经按 tier 分流（interpret.user_store.store_for），但分流是**每个
调用点各自记得**才成立的事，下一个写命令的人一定会忘。所以门设在写入口本身。
"""
import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation,
)
from framework_reader.interpret.store import (
    DEFAULT_ROOT, InterpretationStore, UserFrameworkInContentError,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))


def _interp(control_id: str) -> Interpretation:
    return Interpretation(
        control_id=control_id,
        fields={n: Field(value="x", basis=Basis.INFERRED) for n in ALL_FIELDS},
    )


def _import(framework_id: str):
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id=framework_id, name="我的制度",
        controls=[("4.1", "日志留存", None, "留存六个月。")],
    )


def test_a_user_framework_cannot_be_written_into_the_content_repo():
    _import("ACME-SEC-2026")
    with pytest.raises(UserFrameworkInContentError, match="ACME-SEC-2026"):
        InterpretationStore().save(_interp("ACME-SEC-2026:4.1"))


def test_the_error_says_where_it_should_have_gone():
    _import("ACME-SEC-2026")
    with pytest.raises(UserFrameworkInContentError, match="user.sqlite|用户库"):
        InterpretationStore().save(_interp("ACME-SEC-2026:4.1"))


def test_a_builtin_framework_still_writes_normally(tmp_path, monkeypatch):
    _import("ACME-SEC-2026")
    monkeypatch.chdir(tmp_path)      # 别写进真的内容仓
    store = InterpretationStore(DEFAULT_ROOT)
    store.save(_interp("NIST-CSF-2.0:DE.CM-01"))
    assert store.exists("NIST-CSF-2.0:DE.CM-01")


def test_another_root_is_not_this_door(tmp_path):
    """守的是 content/interpretations/ 这一处，不是这个类。

    测试与迁移都要能把用户框架的 YAML 写到别处去读写。
    """
    _import("ACME-SEC-2026")
    store = InterpretationStore(tmp_path / "somewhere")
    store.save(_interp("ACME-SEC-2026:4.1"))
    assert store.exists("ACME-SEC-2026:4.1")


def test_a_framework_nobody_imported_is_not_blocked(tmp_path, monkeypatch):
    """门是按「这个框架在不在用户库里」判的，不是按编号长相猜的。"""
    monkeypatch.chdir(tmp_path)
    store = InterpretationStore(DEFAULT_ROOT)
    store.save(_interp("SOME-NEW-FRAMEWORK:1.1"))
    assert store.exists("SOME-NEW-FRAMEWORK:1.1")


def test_a_missing_user_library_does_not_break_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "never-created"))
    monkeypatch.chdir(tmp_path)
    store = InterpretationStore(DEFAULT_ROOT)
    store.save(_interp("NIST-CSF-2.0:DE.CM-01"))
    assert store.exists("NIST-CSF-2.0:DE.CM-01")
