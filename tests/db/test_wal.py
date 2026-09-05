"""多人并发写：WAL 与等锁。见 2026-08-23 网页服务化设计 §6⑨

默认的 rollback journal 模式下，一个人在写的时候**读的人也被挡住**——
本机单人无所谓，网页服务上就是「点一下转半天」。WAL 让读写并行。

但 WAL 只解决一半。两个写者仍然要排队，而 SQLite 引擎默认等 0 毫秒：排在后面的
那个不是等，是立刻抛 `database is locked`。Python 的驱动替我们兜了 5 秒，所以
今天并没有坏——我们明写一遍，是把这个值钉住，别让它是一条谁都没意识到自己
依赖过的默认值。
"""
import sqlite3
import threading
import time
from pathlib import Path

import pytest


def _mode(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ---------- 运营库与用户库：开 ----------

def test_the_identity_database_is_in_wal_mode(home):
    from framework_reader.identity.store import IdentityStore

    store = IdentityStore()
    store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                         roles=("admin",))
    assert _mode(store.path) == "wal"


def test_the_user_database_is_in_wal_mode(home):
    from framework_reader.userframework.store import UserFrameworkStore

    store = UserFrameworkStore()
    store.add_framework(framework_id="ACME-1", name="ACME",
                        controls=[("4.1", "日志", None, "正文")])
    assert _mode(store.path) == "wal"


def test_the_assessment_store_shares_that_database(home):
    from framework_reader.assess.store import AssessStore

    store = AssessStore()
    store.record("ACME-1:4.1", level=2)
    assert _mode(store.path) == "wal"


def test_the_model_config_shares_the_operations_database(home):
    from framework_reader.llm.config import ModelConfig

    config = ModelConfig()
    config.set_role("drafter", provider="qwen", model="qwen-max", by="boss")
    assert _mode(config.path) == "wal"


# ---------- 内容包：不开 ----------

def test_the_content_pack_is_not_switched_to_wal(tmp_path):
    """内容包是**要分发的只读文件**。WAL 会在它旁边长出 -wal / -shm 两个
    附属文件，「拷一个文件就能用」立刻不成立。"""
    from framework_reader.pack.db import create_schema

    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.close()
    assert _mode(path) != "wal"


# ---------- 等锁，而不是立刻报错 ----------

def test_a_second_writer_waits_instead_of_erroring(home):
    """默认 busy_timeout 是 0：排在后面的那个不是等，是立刻抛。"""
    from framework_reader.identity.store import IdentityStore

    store = IdentityStore()
    store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                         roles=("admin",))

    # check_same_thread=False：下面那个线程要用它提交。
    blocker = sqlite3.connect(store.path, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO audit_log (at, actor, event, detail) "
                    "VALUES ('t', 'a', 'e', 'd')")

    done = []

    def release() -> None:
        time.sleep(0.4)
        blocker.commit()
        blocker.close()

    threading.Thread(target=release).start()
    store.log("late.writer", actor="ann@acme.cn")     # 不抛就算过
    done.append(True)
    assert done
    assert any(e["event"] == "late.writer" for e in store.audit())


def test_the_timeout_is_pinned_not_inherited(home):
    """值明写在解释它的地方。哪天有人换一种方式建连接，丢掉的不该是一条
    谁都没意识到自己依赖过的驱动默认值。"""
    from framework_reader import sqlite_setup
    from framework_reader.identity.store import IdentityStore

    store = IdentityStore()
    store.create_account(email="boss@acme.cn", password="pw-boss-boss",
                         roles=("admin",))
    conn = sqlite3.connect(store.path, timeout=0)
    sqlite_setup.prepare(conn)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == \
        sqlite_setup.BUSY_TIMEOUT_MS
    conn.close()


# ---------- WAL 之后，只读那条路还得通 ----------

def test_a_wal_user_database_can_still_be_read_read_only(home, tmp_path):
    """QueryAPI 以 mode=ro 打开内容包并 ATTACH 用户库。WAL 库在只读打开时
    需要能碰 -shm 文件——这条断了的话，整个工作台会在导入框架之后白屏。"""
    from framework_reader.pack.db import create_schema
    from framework_reader.query.api import QueryAPI
    from framework_reader.userframework.store import UserFrameworkStore

    content = tmp_path / "content.sqlite"
    conn = sqlite3.connect(content)
    create_schema(conn)
    conn.close()

    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "正文")])
    assert QueryAPI(content).get_framework("ACME-1") is not None


def test_a_reader_is_not_blocked_by_a_writer(home, tmp_path):
    """WAL 要买的就是这个：一个人在写，别人照样能读。"""
    from framework_reader.pack.db import create_schema
    from framework_reader.query.api import QueryAPI
    from framework_reader.userframework.store import UserFrameworkStore

    content = tmp_path / "content.sqlite"
    conn = sqlite3.connect(content)
    create_schema(conn)
    conn.close()

    store = UserFrameworkStore()
    store.add_framework(framework_id="ACME-1", name="ACME 制度",
                        controls=[("4.1", "日志留存", None, "正文")])

    writer = sqlite3.connect(store.path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO user_framework (id, name, version, imported_at) "
                   "VALUES ('ACME-2', 'B', '', 't')")
    try:
        assert QueryAPI(content).get_framework("ACME-1") is not None
    finally:
        writer.rollback()
        writer.close()
