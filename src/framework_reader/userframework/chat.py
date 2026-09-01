"""条款详情页上和 AI 的对话。

**对话跟着条款走，不跟着人走。** 这个产品是一个安全团队协作一套材料
（网页服务化设计 §3），签字的人要能看到「这句话当初是怎么来的」——
那比任何审计记录都管用。

**模型说的话永远不会自己进库。** 它的修改建议存在 `proposal` 里，
`applied_at` 有值才算写过——中间隔着一次人点头。
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
    """把官方映射边组装成对话上下文里的几行——一份「只能照抄」的清单。

    对话引用官方映射是工具和裸问模型的分界线：裸问模型会说
    「通常对应 A.12.4」，编的。这里每一行都带着库里的出处。
    清单为空就明说没有——空清单加提示词里那句不许编，才堵得住幻觉。
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
        """给模型看的那几轮。**必须封顶**——每一句都要把历史重新喂一遍，
        不封顶的话聊得越久每句越贵，三小时前那个已经放弃的说法还会一直跟着。
        """
        got = self._select(
            "SELECT * FROM control_chat WHERE control_id = ?"
            " ORDER BY at DESC, id DESC LIMIT ?", (control_id, turns))
        return list(reversed(got))

    def turn(self, turn_id: str) -> Turn | None:
        found = self._select("SELECT * FROM control_chat WHERE id = ?", (turn_id,))
        return found[0] if found else None

    def mark_applied(self, turn_id: str) -> bool:
        """回 False 表示这一条已经写过了。

        点两次「确定」不该写两次库、记两条审计——而刷新页面就会重发一次 POST。
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
