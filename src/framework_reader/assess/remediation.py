"""Read and write the remediation ledger. The gap report says "what next"; this records "who, by when, done or not".

The ledger is downstream of self-assessment: any gap-report item can be filed for follow-up. The three
states are **flipped by hand**, deliberately not linked to assessment - before a re-assessment, "done" is
only the owner's word; the re-assessed level is the evidence. Whether a row stays in the ledger after a
re-assessment raises the level is a human decision (keeping it says "fixed and verified"; deleting says "closed").
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel

# Display names share a root with the SoA states on the assess page, so nobody learns two vocabularies.
STATES = ("todo", "doing", "done")
STATE_LABELS = {"todo": "To do", "doing": "In progress", "done": "Done"}


class Remediation(BaseModel):
    control_id: str
    scope: str = "default"
    owner: str = ""
    due: str = ""       # ISO date; empty = no deadline set
    state: str = "todo"
    note: str = ""
    created_at: str = ""
    updated_at: str = ""


class RemediationStore:
    """The table lives in user_schema.sql, created idempotently by userframework.store.connect -
    existing databases are patched on open - no migration command needed."""

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
        """File it. **An already-filed row is left exactly as it is** - clicking "file" twice must not wipe the
        owner and deadline the user entered. Returns whether a row was actually created."""
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
        """Deadline rows first (tightest first); rows without a deadline follow, sorted by control id."""
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
        """Update only the fields given. None = not given, which is not the same as "clear"."""
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
