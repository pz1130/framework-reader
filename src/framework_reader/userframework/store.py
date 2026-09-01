"""用户导入的框架。主 spec §6.1、§7.3.5

写的是用户库，**内容包一个字节都不动**——内容包是你发布的只读文件，
随时可以 `make build` 重建；用户导入的东西不能被那一下抹掉。
"""
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from framework_reader import sqlite_setup

SCHEMA = Path(__file__).resolve().parent.parent / "pack" / "user_schema.sql"


class UserFramework(BaseModel):
    id: str
    name: str
    version: str = ""
    imported_at: datetime
    source_file: str = ""
    controls: int = 0


def default_path() -> Path:
    from framework_reader import usage

    return usage.home() / "user.sqlite"


def connect(path: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """打开用户库。`create=False` 时库不存在就返回 None——查询层用得上。

    建表语句一律 `IF NOT EXISTS`，所以「建库」和「补表」是同一件事，且可以
    重复执行。这不只是省代码：网页服务上两个人在全新安装上同时写，会各自
    去建一次表，靠「文件在不在」分支的写法这时会有一个人拿到
    `table ... already exists` 而失败。
    """
    target = Path(path) if path else default_path()
    if not target.exists() and not create:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    sqlite_setup.prepare(conn)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _add_missing_columns(conn)
    conn.commit()
    return conn


# 建表语句是 IF NOT EXISTS，所以**已有的库不会因为 schema 加了一列就跟着变**。
# 加列必须单独说一遍。写成「读一遍 PRAGMA，缺哪列补哪列」而不是版本号迁移：
# 这里每一步都幂等，重复跑无害，也没有「迁移到第几版」的状态要维护。
_ADDED_COLUMNS = (
    ("user_document", "title", "TEXT NOT NULL DEFAULT ''"),
    ("user_document", "chars", "INTEGER NOT NULL DEFAULT 0"),
    ("user_document", "uploaded_by", "TEXT NOT NULL DEFAULT ''"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _ADDED_COLUMNS:
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


class UserFrameworkStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()

    def _conn(self) -> sqlite3.Connection:
        conn = connect(self.path)
        assert conn is not None
        return conn

    def add_framework(
        self,
        *,
        framework_id: str,
        name: str,
        controls: Sequence[tuple[str, str, str | None, str]],
        version: str = "",
        source_file: str = "",
    ) -> UserFramework:
        """controls 是 (本地编号, 标题, 父编号, 正文) 四元组，顺序即展示顺序。"""
        now = datetime.now(timezone.utc)
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO user_framework (id, name, version, imported_at, source_file) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, version=excluded.version, "
                "imported_at=excluded.imported_at, source_file=excluded.source_file",
                (framework_id, name, version, now.isoformat(), source_file),
            )
            conn.execute("DELETE FROM user_control WHERE framework_id = ?", (framework_id,))
            conn.executemany(
                "INSERT INTO user_control "
                "(id, framework_id, label, parent_id, body, sort_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"{framework_id}:{local}", framework_id, label,
                        f"{framework_id}:{parent}" if parent else None, body, index,
                    )
                    for index, (local, label, parent, body) in enumerate(controls)
                ],
            )
            conn.commit()
            return UserFramework(
                id=framework_id, name=name, version=version, imported_at=now,
                source_file=source_file, controls=len(controls),
            )
        finally:
            conn.close()

    def list_frameworks(self) -> list[UserFramework]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT f.*, (SELECT COUNT(*) FROM user_control c "
                "  WHERE c.framework_id = f.id) AS controls "
                "FROM user_framework f ORDER BY f.imported_at"
            ).fetchall()
            return [UserFramework(**dict(r)) for r in rows]
        finally:
            conn.close()

    def control_ids(self, framework_id: str) -> set[str]:
        conn = self._conn()
        try:
            return {
                r[0] for r in conn.execute(
                    "SELECT id FROM user_control WHERE framework_id = ?", (framework_id,)
                )
            }
        finally:
            conn.close()

    def load_body(self, control_id: str) -> str | None:
        """这条的正文。导入条款看 user_control，内置条款看覆盖层；
        都没有（内置从未贴过、编号写错）返回 None。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT body FROM user_control WHERE id = ?", (control_id,)
            ).fetchone()
            if row:
                return row[0]
            row = conn.execute(
                "SELECT body FROM control_body_override WHERE control_id = ?",
                (control_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def update_body(self, control_id: str, body: str) -> None:
        """改正文，两层分流：导入条款改 user_control 本行；内置条款写
        control_body_override 覆盖层——内容库（官方基准、进 git、pack 可重建）
        一个字节不动，删行即恢复默认。original_text 那块墓碑不受影响：
        贴进来的原文进的是用户自己的库，不出服务器（§7(A) 边界不变）。"""
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE user_control SET body = ? WHERE id = ?",
                (body, control_id))
            if cur.rowcount:
                conn.commit()
                return
            if body:
                conn.execute(
                    "INSERT INTO control_body_override (control_id, body, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(control_id) DO UPDATE SET "
                    "body=excluded.body, updated_at=excluded.updated_at",
                    (control_id, body,
                     datetime.now(timezone.utc).isoformat()))
            else:
                # 空 = 清空。内置条款上正好读作「恢复官方默认」——
                # 和 all_interpretation 清空字段回内容包版本是同一个语义。
                conn.execute(
                    "DELETE FROM control_body_override WHERE control_id = ?",
                    (control_id,))
            conn.commit()
        finally:
            conn.close()

    # 挂在 control_id 上的那些表。删框架要连它们一起删——
    # **留下够不着的孤儿行，下次导入同名框架还会串味**：编号一样，
    # 答案是上一份文档的，而没人会想到去怀疑它。
    _BY_CONTROL = (
        "user_interpretation", "user_interpretation_meta",
        "assessment", "answer_history", "user_annotation",
    )

    def what_removing_costs(self, framework_id: str) -> dict[str, int]:
        """删之前先说清楚会丢掉什么。**无声的破坏是最坏的那种。**"""
        conn = self._conn()
        try:
            ids = "SELECT id FROM user_control WHERE framework_id = ?"
            out = {"controls": conn.execute(
                "SELECT COUNT(*) FROM user_control WHERE framework_id = ?",
                (framework_id,)).fetchone()[0]}
            for table, key in (("assessment", "assessments"),
                               ("user_interpretation", "interpretations")):
                out[key] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE control_id IN ({ids})",
                    (framework_id,)).fetchone()[0]
            out["confirmations"] = conn.execute(
                f"SELECT COUNT(*) FROM confirmation WHERE target_id IN ({ids})",
                (framework_id,)).fetchone()[0]
            return out
        finally:
            conn.close()

    def remove(self, framework_id: str) -> None:
        """连同它的条款、解读、自评、签字一起删。

        原来只删条款和解读，自评与签字留在库里——而这个方法自己的注释就写着
        「留下够不着的孤儿行，下次导入同名框架还会串味」。同一个理由对自评
        同样成立，只是当初漏了。
        """
        conn = self._conn()
        try:
            ids = "SELECT id FROM user_control WHERE framework_id = ?"
            for table in self._BY_CONTROL:
                conn.execute(
                    f"DELETE FROM {table} WHERE control_id IN ({ids})",
                    (framework_id,))
            conn.execute(
                f"DELETE FROM confirmation WHERE target_id IN ({ids})",
                (framework_id,))
            conn.execute("DELETE FROM user_control WHERE framework_id = ?",
                         (framework_id,))
            conn.execute("DELETE FROM user_framework WHERE id = ?", (framework_id,))
            conn.commit()
        finally:
            conn.close()
