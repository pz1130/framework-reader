"""Conversation with the AI on the clause detail page.

**The conversation belongs to the clause, not to the person.** This product is a
security team collaborating on one body of material (hosted-service design §3);
the person signing needs to be able to see "where this sentence originally came
from" - that beats any audit record.

**What the model says never enters the store on its own.** Its edit suggestions
are kept in `proposal`; only a non-empty `applied_at` counts as written - with
one human nod in between.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.userframework.store import connect


@dataclass
class Turn:
    turn_id: str
    control_id: str
    at: str
    actor: str
    role: str                       # user | ai
    text: str
    proposal: list = field(default_factory=list)
    applied: bool = False


def mapping_lines(neighbors) -> list[str]:
    """Assemble the official mapping edges into a few lines for the chat context -
    a "copy verbatim" list.

    Citing official mappings is the line between a tool and asking a bare model:
    a bare model will say "usually maps to A.12.4" - invented. Here every line
    carries its source in the library. When the list is empty, say so plainly -
    an empty list plus the prompt's "do not invent" is what holds the
    hallucination back.
    """
    if not neighbors:
        return ["Official mappings: (none found in the library for this control. "
                "If asked which clause it maps to, say so plainly - do not invent one.)"]
    return [f"- {n.control_id} {n.label} ({n.relation}, source {n.source})"
            for n in neighbors]


class ChatStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None

    def _conn(self):
        conn = connect(self.path)
        assert conn is not None
        return conn

    def say(self, control_id: str, *, role: str, text: str, actor: str = "",
            proposal: list | None = None) -> str:
        turn_id = uuid.uuid4().hex
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO control_chat (id, control_id, at, actor, role,"
                " text, proposal, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, '')",
                (turn_id, control_id, datetime.now(timezone.utc).isoformat(),
                 actor, role, text,
                 json.dumps(proposal or [], ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        return turn_id

    def history(self, control_id: str) -> list[Turn]:
        return self._select(
            "SELECT * FROM control_chat WHERE control_id = ? ORDER BY at, id",
            (control_id,))

    def recent(self, control_id: str, turns: int = 6) -> list[Turn]:
        """The turns the model sees. **Must be capped** - every message re-feeds
        the whole history; uncapped, the longer the chat runs the more each
        message costs, and an approach abandoned three hours ago keeps tagging
        along.
        """
        got = self._select(
            "SELECT * FROM control_chat WHERE control_id = ?"
            " ORDER BY at DESC, id DESC LIMIT ?", (control_id, turns))
        return list(reversed(got))

    def turn(self, turn_id: str) -> Turn | None:
        found = self._select("SELECT * FROM control_chat WHERE id = ?", (turn_id,))
        return found[0] if found else None

    def mark_applied(self, turn_id: str) -> bool:
        """Returning False means this one was already applied.

        Clicking "Confirm" twice must not write the store twice or log two audit
        entries - and refreshing the page re-sends the POST.
        """
        conn = self._conn()
        try:
            changed = conn.execute(
                "UPDATE control_chat SET applied_at = ?"
                " WHERE id = ? AND applied_at = ''",
                (datetime.now(timezone.utc).isoformat(), turn_id)).rowcount
            conn.commit()
        finally:
            conn.close()
        return changed > 0

    def _select(self, sql: str, args: tuple) -> list[Turn]:
        conn = self._conn()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        return [
            Turn(turn_id=r["id"], control_id=r["control_id"], at=r["at"],
                 actor=r["actor"], role=r["role"], text=r["text"],
                 proposal=json.loads(r["proposal"] or "[]"),
                 applied=bool(r["applied_at"]))
            for r in rows
        ]
