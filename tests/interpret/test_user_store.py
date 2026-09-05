"""导入框架的解读落用户库，不落 content/。主 spec §7.3.5

b971e12 之前起草导入的框架，YAML 会写进 `content/interpretations/<框架>/`——
用户自己公司的制度解读进了我们的内容仓，还被 git 追踪。方向反了：
内容仓是我们发布的东西，用户数据一个字都不该在里面。
"""
import sqlite3

import pytest

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    ModelRef,
)
from framework_reader.interpret.user_store import UserInterpretationStore, store_for
from framework_reader.schema.entities import Framework, LicenseTier


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _interp(control_id="ACME-SEC-2026:4.1", **kwargs) -> Interpretation:
    return Interpretation(
        control_id=control_id,
        fields={
            name: Field(
                value={"1": "一档", "2": "二档", "3": "三档"} if name == "practice" else "x",
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
        **kwargs,
    )


def test_a_saved_interpretation_comes_back(home):
    store = UserInterpretationStore()
    store.save(_interp())
    assert store.load("ACME-SEC-2026:4.1").fields["intent"].value == "x"


def test_a_structured_field_survives_the_round_trip():
    """practice 是三档字典。存成字符串的话差距报告就没有下一步可查。"""
    store = UserInterpretationStore()
    store.save(_interp())
    assert store.load("ACME-SEC-2026:4.1").fields["practice"].value["2"] == "二档"


def test_exists_is_false_before_and_true_after():
    store = UserInterpretationStore()
    assert not store.exists("ACME-SEC-2026:4.1")
    store.save(_interp())
    assert store.exists("ACME-SEC-2026:4.1")


def test_which_model_drafted_it_is_kept():
    """成色可读是这个产品的立身之本，起草它的模型不能存丢。"""
    store = UserInterpretationStore()
    store.save(_interp(provenance=InterpretationProvenance(
        drafter=ModelRef(provider="deepseek", model="deepseek-chat", prompt_version="v3")
    )))
    assert store.load("ACME-SEC-2026:4.1").provenance.drafter.model == "deepseek-chat"


def test_the_state_is_kept():
    """签过字的解读不能读回来变成初稿——页面靠 state 决定要不要打「AI 初稿」。"""
    from datetime import datetime, timezone

    store = UserInterpretationStore()
    store.save(_interp(
        state=InterpretationState.CONFIRMED,
        provenance=InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        ),
    ))
    assert store.load("ACME-SEC-2026:4.1").state is InterpretationState.CONFIRMED


def test_saving_twice_does_not_leave_stale_field_rows():
    store = UserInterpretationStore()
    store.save(_interp())
    store.save(_interp())
    assert len(store.load("ACME-SEC-2026:4.1").fields) == len(ALL_FIELDS)


def test_loading_something_that_was_never_drafted_says_so():
    with pytest.raises(FileNotFoundError, match="ACME-SEC-2026:9.9"):
        UserInterpretationStore().load("ACME-SEC-2026:9.9")


def test_nothing_is_written_under_content(tmp_path, monkeypatch):
    """真正要防的事：用户的解读出现在产品的内容仓里。"""
    monkeypatch.chdir(tmp_path)
    UserInterpretationStore().save(_interp())
    assert not (tmp_path / "content").exists()


# ---------- 按 tier 选存储 ----------

def _view(tier):
    return Framework(
        id="X", name="X", version="1", tier=tier, source_url="u", license_note="n"
    )


def test_an_imported_framework_drafts_into_the_user_layer():
    assert isinstance(store_for(_view(LicenseTier.U_USER)), UserInterpretationStore)


def test_a_builtin_framework_still_drafts_into_content():
    from framework_reader.interpret.store import InterpretationStore

    assert isinstance(store_for(_view(LicenseTier.A_EMBEDDABLE)), InterpretationStore)


# ---------- 老库迁移 ----------

def test_an_old_user_database_gets_the_new_tables(home):
    """老库里有 user_framework。以它作总判据的话，新表永远建不出来。"""
    from framework_reader.userframework.store import connect, default_path

    home.mkdir(parents=True, exist_ok=True)
    path = default_path()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE user_framework (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()

    conn = connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "user_interpretation" in tables and "user_interpretation_meta" in tables


# ---------- 与 InterpretationStore 对等的遍历接口 ----------

def test_iter_all_yields_what_was_saved():
    """proofread 这类按框架遍历的命令要能对用户框架也用。"""
    store = UserInterpretationStore()
    store.save(_interp("ACME-SEC-2026:4.1"))
    store.save(_interp("ACME-SEC-2026:4.2"))
    assert sorted(i.control_id for i in store.iter_all()) == [
        "ACME-SEC-2026:4.1", "ACME-SEC-2026:4.2",
    ]


def test_by_state_filters():
    from datetime import datetime, timezone

    store = UserInterpretationStore()
    store.save(_interp("ACME-SEC-2026:4.1"))
    store.save(_interp(
        "ACME-SEC-2026:4.2", state=InterpretationState.CONFIRMED,
        provenance=InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)),
    ))
    drafts = store.by_state(InterpretationState.DRAFT)
    assert [i.control_id for i in drafts] == ["ACME-SEC-2026:4.1"]


def test_iter_all_on_an_empty_library_is_empty():
    assert list(UserInterpretationStore().iter_all()) == []
