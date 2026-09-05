"""Frameworks imported by users. Main spec §6.1, §7.3.5

This writes the user store; **the content pack is not touched by a single byte** -
the content pack is a read-only file you publish and can rebuild any time with
`make build`; user-imported material must not be wiped by that step.
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
    """Open the user store. With `create=False`, returns None when the store does not
    exist - the query layer needs that.

    Every DDL statement is `IF NOT EXISTS`, so "create the store" and "add missing
    tables" are the same operation, and it can run repeatedly. This is not just
    saving code: on the hosted service two people writing concurrently on a fresh
    install will each try to create the tables; with a "does the file exist" branch,
    one of them fails with `table ... already exists`.
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


# The DDL is IF NOT EXISTS, so **an existing store does not pick up a column added
# to the schema**. New columns must be stated separately. This is written as "read
# the PRAGMA once, add whichever columns are missing" rather than versioned
# migrations: every step here is idempotent, re-running is harmless, and there is no
# "which migration version" state to maintain.
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
        """controls is a sequence of (local id, title, parent id, body) tuples; the order is the display order."""
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
        """The body of this control. Imported controls live in user_control, built-in
        ones in the override layer; when neither has it (a built-in never pasted, or a
        mistyped id), return None."""
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
        """Edit the body, routed in two layers: imported controls update their
        user_control row; built-in controls write to the control_body_override layer -
        the content store (the official baseline, committed to git, rebuildable from
        the pack) is untouched byte for byte, and deleting the row restores the
        default. The original_text tombstone is unaffected: pasted source text goes
        into the user's own store and never leaves the server (§7(A) boundary
        unchanged)."""
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
                # Empty = clear. On a built-in control this reads exactly as "restore
                # the official default" - the same semantics as clearing a field in
                # all_interpretation falling back to the content-pack version.
                conn.execute(
                    "DELETE FROM control_body_override WHERE control_id = ?",
                    (control_id,))
            conn.commit()
        finally:
            conn.close()

    # The tables keyed by control_id. Removing a framework must remove these too -
    # **orphaned rows nothing can reach would poison the next import of a same-named
    # framework**: identical numbers, answers from the previous document, and nobody
    # would ever think to suspect them.
    _BY_CONTROL = (
        "user_interpretation", "user_interpretation_meta",
        "assessment", "answer_history", "user_annotation",
    )

    def what_removing_costs(self, framework_id: str) -> dict[str, int]:
        """Spell out what will be lost before deleting. **Silent destruction is the worst kind.**"""
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
        """Delete the framework together with its controls, interpretations,
        assessments, and confirmations.

        This used to delete only controls and interpretations, leaving assessments and
        confirmations in the store - even though this very class's comment said
        "orphaned rows nothing can reach would poison the next import of a same-named
        framework". The same argument applies to assessments; it was simply missed
        originally.
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
