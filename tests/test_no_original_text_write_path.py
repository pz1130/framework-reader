"""受版权原文的注入路径永远不建。网页服务化设计 §7(A)

本地版的版权边界是「骨架内置、原文外挂」：用户在**自己的机器上**注入自己
购买的那份原文。产品改成联网服务之后这条边界断了——那份原文会存在我们的
服务器上。§7 的取舍是 (A)：**彻底不建这条路**。

它当时是零成本的（一行都没实现），代价全在将来：三个月后有人看见
`original_text` 这张空表，会以为它是个待填的坑。这两条测试就是那张表的墓碑。
"""
import re
import sqlite3
from pathlib import Path

import pytest

from framework_reader.userframework import store

SRC = Path("src/framework_reader")


def test_user_db_rejects_original_text_insert(tmp_path):
    """库自己拒绝。门开在存储层——绕过 store 直接写 SQL 也拦得住。"""
    conn = store.connect(tmp_path / "user.sqlite")
    assert conn is not None

    with pytest.raises(sqlite3.IntegrityError) as exc:
        conn.execute(
            "INSERT INTO original_text (control_id, locale, body) VALUES (?, ?, ?)",
            ("A.5.1", "en", "Information security policy shall be defined…"),
        )
    assert "original_text" in str(exc.value)

    (count,) = conn.execute("SELECT COUNT(*) FROM original_text").fetchone()
    assert count == 0
    conn.close()


def test_no_code_writes_to_original_text():
    """代码里没有写入语句。测试拦住的是「谁哪天顺手把这条路补上」。"""
    pattern = re.compile(
        r"(INSERT\s+(OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE)\s+original_text",
        re.IGNORECASE,
    )
    offenders = [
        str(path)
        for path in [*SRC.rglob("*.py"), *SRC.rglob("*.sql")]
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"这些文件往 original_text 写：{offenders}"


def test_old_db_gets_the_door_on_next_open(tmp_path):
    """这道门是加在已经发出去的库上的——建表语句每次连库都重跑一遍，
    所以「补一道触发器」和「建一个新库」是同一件事，不需要迁移脚本。"""
    path = tmp_path / "user.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE original_text ("
        "control_id TEXT NOT NULL, locale TEXT NOT NULL, body TEXT NOT NULL,"
        " PRIMARY KEY (control_id, locale))"
    )
    raw.execute(
        "INSERT INTO original_text (control_id, locale, body) VALUES ('A.5.1','en','旧库里已经有的')"
    )
    raw.commit()
    raw.close()

    conn = store.connect(path)
    assert conn is not None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO original_text (control_id, locale, body) VALUES ('A.5.2','en','新的')"
        )
    # 已经在里面的那行不动：删用户机器上的数据不是这条断言该做的事，
    # 它只保证不再有新的进来。
    (count,) = conn.execute("SELECT COUNT(*) FROM original_text").fetchone()
    assert count == 1
    conn.close()
