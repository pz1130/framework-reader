"""Preview state for document import. See the 2026-08-25 AI import design §3

**On disk, not in memory.** The job state in `web/jobs.py` may be lost, because
the drafting results are already in the user store - all that is lost is "which
item it got to". Here the results are not in the store yet; losing them is
money spent for nothing, and the user will not know why, only pay again.

Drafts are temporary: deleted once confirmed or abandoned. No expiry sweep -
out of scope for phase 1. A draft left behind is invisible on every page; it is
just a few rows of text.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.userframework.outline import Problem, Span
from framework_reader.userframework.store import connect


@dataclass
class ImportDraft:
    draft_id: str
    framework_id: str
    name: str
    source_text: str
    spans: list[Span]
    problems: list[Problem]
    # Unchecked clauses are stored as the **string of the list index**. The
    # number is not used as the key: numbers can be empty or duplicated, and a
    # key would collide.
    dropped: set[str] = field(default_factory=set)


class ImportDraftStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None

    def _conn(self):
        conn = connect(self.path)
        assert conn is not None
        return conn

    def create(self, *, framework_id: str, name: str, source_text: str,
               spans: list[Span], problems: list[Problem], actor: str) -> str:
        draft_id = uuid.uuid4().hex
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO import_draft (id, framework_id, name, source_text,"
                " spans, dropped, problems, created_at, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, framework_id, name, source_text,
                 _dump_spans(spans), "[]", _dump_problems(problems),
                 datetime.now(timezone.utc).isoformat(), actor))
            conn.commit()
        finally:
            conn.close()
        return draft_id

    def load(self, draft_id: str) -> ImportDraft | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM import_draft WHERE id = ?", (draft_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ImportDraft(
            draft_id=row["id"], framework_id=row["framework_id"],
            name=row["name"], source_text=row["source_text"],
            spans=_load_spans(row["spans"]),
            problems=_load_problems(row["problems"]),
            dropped=set(json.loads(row["dropped"])),
        )

    def save(self, draft_id: str, *, spans: list[Span], dropped: set[str]) -> None:
        """Changes only the cut and the checkboxes. **The source snapshot is never
        touched** - body text still gets cut from it."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE import_draft SET spans = ?, dropped = ? WHERE id = ?",
                (_dump_spans(spans), json.dumps(sorted(dropped)), draft_id))
            conn.commit()
        finally:
            conn.close()

    def delete(self, draft_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM import_draft WHERE id = ?", (draft_id,))
            conn.commit()
        finally:
            conn.close()


def _dump_spans(spans: list[Span]) -> str:
    return json.dumps([
        {"ref": s.ref, "label": s.label, "parent": s.parent,
         "start": s.start, "end": s.end,
         # "Who wrote it" must be persisted along - lose it and it becomes "was in the source all along".
         "ref_from": s.ref_from, "label_from": s.label_from}
        for s in spans], ensure_ascii=False)


def _load_spans(raw: str) -> list[Span]:
    return [Span(**row) for row in json.loads(raw)]


def _dump_problems(problems: list[Problem]) -> str:
    return json.dumps([{"kind": p.kind, "detail": p.detail} for p in problems],
                      ensure_ascii=False)


def _load_problems(raw: str) -> list[Problem]:
    return [Problem(**row) for row in json.loads(raw)]
