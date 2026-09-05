"""Read/write access to self-assessment data. Main spec §6.1

**The database here and the content pack are two separate files.** The content pack can
be rebuilt any time with `make clean`; the user's self-assessment data must not vanish
along with it. So it lands under $FRAMEWORK_READER_HOME by default, not in the build
directory — this is not fastidiousness, it is data safety.
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
            # Every CREATE statement is IF NOT EXISTS, so "creating the database" and
            # "filling in missing tables" are the same operation, and running it on every
            # connection is harmless — when the schema gains new tables, existing
            # databases catch up automatically. Same approach as
            # userframework.store.connect; see the remarks there.
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
        # History is append-only, never updated: what a re-assessment comparison wants is
        # "what it was at the moment of each recording". Recording a mistake and
        # re-recording on the spot is fine — adjacent identical values get folded away in
        # the comparison.
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
        """Re-assessment comparison: controls whose level or applicability differs from
        the previous recording, sorted by most recent change.

        A "value" is a short human-readable phrase, one of: not applicable / level N /
        SoA status; adjacent identical records are folded away, so a mistake corrected on
        the spot never shows up in the comparison. The history table only started being
        recorded today, so existing databases have nothing here until they have been
        re-assessed once — this is an honest empty, not a broken one.
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
            # Fold adjacent identical values: append a new entry only on a real change.
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
    """The "value" of one assessment, a short phrase for humans. Same wording as the
    "recorded" display on the self-assessment page."""
    if not applicable:
        return "N/A"
    if level is not None:
        return f"L{level}"
    return status or "Not assessed"
