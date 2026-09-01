"""自评数据的读写。主 spec §6.1

**这里的库和内容包是两个文件。** 内容包可以随时 `make clean` 重建，
用户自评数据不能跟着没。所以默认落在 $FRAMEWORK_READER_HOME 下，
不在构建目录里——这条不是洁癖，是数据安全。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from framework_reader import sqlite_setup

SCHEMA = Path(__file__).resolve().parent.parent / "pack" / "user_schema.sql"


class Assessment(BaseModel):
    control_id: str
    scope: str = "default"
    applicable: bool = True
    reason: str = ""
    level: int | None = None
    status: str = ""
    note: str = ""
    assessed_at: datetime


def default_path() -> Path:
    from framework_reader import usage

    return usage.home() / "user.sqlite"


class AssessStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            sqlite_setup.prepare(self._conn)
            # 建表语句一律 IF NOT EXISTS，所以「建库」和「补表」是同一件事，
            # 每次连接都跑一遍也无害——schema 加了新表，已存在的库自动跟上。
            # 与 userframework.store.connect 同一个做法，见那里的话。
            self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            self._conn.commit()
        return self._conn

    def record(
        self,
        control_id: str,
        *,
        scope: str = "default",
        applicable: bool = True,
        reason: str = "",
        level: int | None = None,
        status: str = "",
        note: str = "",
    ) -> Assessment:
        entry = Assessment(
            control_id=control_id, scope=scope, applicable=applicable, reason=reason,
            level=level, status=status, note=note,
            assessed_at=datetime.now(timezone.utc),
        )
        conn = self._connect()
        conn.execute(
            "INSERT INTO assessment "
            "(control_id, scope, applicable, reason, level, status, note, assessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(control_id, scope) DO UPDATE SET "
            "applicable=excluded.applicable, reason=excluded.reason, "
            "level=excluded.level, status=excluded.status, note=excluded.note, "
            "assessed_at=excluded.assessed_at",
            (control_id, scope, int(applicable), reason, level, status, note,
             entry.assessed_at.isoformat()),
        )
        # 历史只追加、永不更新：复评对比要的就是「每次记下时是多少」。
        # 记错的当场重记一次也没关系——相邻同值在对比里会被折叠掉。
        conn.execute(
            "INSERT INTO assessment_history "
            "(control_id, scope, applicable, level, status, assessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (control_id, scope, int(applicable), level, status,
             entry.assessed_at.isoformat()),
        )
        conn.commit()
        return entry

    def get(self, control_id: str, scope: str = "default") -> Assessment | None:
        row = self._connect().execute(
            "SELECT * FROM assessment WHERE control_id = ? AND scope = ?",
            (control_id, scope),
        ).fetchone()
        return self._row(row) if row else None

    def all(self, scope: str = "default") -> list[Assessment]:
        rows = self._connect().execute(
            "SELECT * FROM assessment WHERE scope = ? ORDER BY control_id", (scope,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def changes(self, scope: str = "default", *, limit: int = 50) -> list[dict]:
        """复评对比：档位或适用性跟上一记不一样了的条款，按最近变动排。

        「值」= 不适用 / N 档 / SoA 状态三选一的中文短句；相邻相同的记录折叠掉，
        所以当场记错重记一次不会出现在对比里。历史表是今天才开始记的，
        已有的库要复评过一次之后这里才有东西——这是诚实的空，不是坏的空。
        """
        rows = self._connect().execute(
            "SELECT control_id, applicable, level, status, assessed_at "
            "FROM assessment_history WHERE scope = ? "
            "ORDER BY control_id, assessed_at", (scope,)
        ).fetchall()
        runs: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            value = _value_label(r["applicable"], r["level"], r["status"])
            timeline = runs.setdefault(r["control_id"], [])
            # 相邻同值折叠：只有真变了才追加一记。
            if not timeline or timeline[-1][0] != value:
                timeline.append((value, r["assessed_at"]))
        out = []
        for control_id, timeline in runs.items():
            if len(timeline) < 2:
                continue
            (old, _), (new, at) = timeline[-2], timeline[-1]
            out.append({
                "control_id": control_id, "from": old, "to": new, "at": at,
            })
        out.sort(key=lambda c: c["at"], reverse=True)
        return out[:limit]

    @staticmethod
    def _row(row: sqlite3.Row) -> Assessment:
        data = dict(row)
        data["applicable"] = bool(data["applicable"])
        return Assessment(**data)


def _value_label(applicable: int, level: int | None, status: str) -> str:
    """一次自评的「值」，给人看的短句。和自评页的「已记」口径一致。"""
    if not applicable:
        return "N/A"
    if level is not None:
        return f"L{level}"
    return status or "Not assessed"
