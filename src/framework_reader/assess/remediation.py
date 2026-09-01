"""整改台账的读写。差距报告说「下一步做什么」，这里记「谁、什么时候、做了没有」。

台账是自评的下游：差距报告每一条都可以立项跟进。state 三档**手工扳**，
不跟自评联动——改完没复评之前，「做了」只是当事人的一面之词，复评的档位
才是证据。复评把档位提上去之后这条还在不在台账里，由人决定（留着的意义是
「改了，验证过了」，删掉的意义是「这事完结了」，两边都有道理）。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel

# 展示名跟自评页的 SoA 状态用一个词根，人不用学两套话。
STATES = ("todo", "doing", "done")
STATE_LABELS = {"todo": "To do", "doing": "In progress", "done": "Done"}


class Remediation(BaseModel):
    control_id: str
    scope: str = "default"
    owner: str = ""
    due: str = ""       # ISO 日期，空 = 没定期限
    state: str = "todo"
    note: str = ""
    created_at: str = ""
    updated_at: str = ""


class RemediationStore:
    """表在 user_schema.sql，跟着 userframework.store.connect 的幂等建表走——
    已有的库打开就补上，不需要迁移命令。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else _default_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            from framework_reader.userframework.store import connect

            self._conn = connect(self.path)
        return self._conn

    def start(self, control_id: str, *, scope: str = "default",
              owner: str = "", due: str = "") -> bool:
        """立项。**已立项的条款原样留着**——重复点「立项」不该把人填的
        负责人跟期限冲掉。返回是不是真建了一行。"""
        conn = self._connect()
        now = _now()
        cur = conn.execute(
            "INSERT OR IGNORE INTO remediation "
            "(control_id, scope, owner, due, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'todo', ?, ?)",
            (control_id, scope, owner.strip(), due.strip(), now, now),
        )
        conn.commit()
        return cur.rowcount > 0

    def get(self, control_id: str, scope: str = "default") -> Remediation | None:
        row = self._connect().execute(
            "SELECT * FROM remediation WHERE control_id = ? AND scope = ?",
            (control_id, scope),
        ).fetchone()
        return Remediation(**dict(row)) if row else None

    def all(self, scope: str = "default") -> list[Remediation]:
        """有期限的在前（紧的先做），没期限的按条款号排在其后。"""
        rows = self._connect().execute(
            "SELECT * FROM remediation WHERE scope = ? "
            "ORDER BY (due = ''), due, control_id", (scope,),
        ).fetchall()
        return [Remediation(**dict(r)) for r in rows]

    def update(
        self, control_id: str, *, scope: str = "default",
        state: str | None = None, owner: str | None = None,
        due: str | None = None, note: str | None = None,
    ) -> Remediation | None:
        """只改给了的字段。None = 没给，跟「清空」是两回事。"""
        if state is not None and state not in STATES:
            raise ValueError(f"Unknown remediation state: {state}")
        current = self.get(control_id, scope)
        if current is None:
            return None
        conn = self._connect()
        conn.execute(
            "UPDATE remediation SET owner = ?, due = ?, state = ?, note = ?, "
            "updated_at = ? WHERE control_id = ? AND scope = ?",
            (current.owner if owner is None else owner.strip(),
             current.due if due is None else due.strip(),
             current.state if state is None else state,
             current.note if note is None else note.strip(),
             _now(), control_id, scope),
        )
        conn.commit()
        return self.get(control_id, scope)

    def remove(self, control_id: str, scope: str = "default") -> bool:
        conn = self._connect()
        cur = conn.execute(
            "DELETE FROM remediation WHERE control_id = ? AND scope = ?",
            (control_id, scope),
        )
        conn.commit()
        return cur.rowcount > 0


def _default_path() -> Path:
    from framework_reader import usage

    return usage.home() / "user.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
