"""文档导入的预览态。见 2026-08-25 AI 导入设计 §3

**落盘，不在内存。** `web/jobs.py` 的任务状态可以丢，因为起草的结果已经在
用户库里，丢的只是「跑到第几条」；这里的结果还没进库，丢了就是白花的钱，
而且用户不会知道为什么，只能再花一次。

草稿是临时的：确认或放弃后删除。没有过期回收——一期不做，留着的草稿
在任何页面上都看不见，它只是几行文本。
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
    # 被取消勾选的条款，存的是**列表下标**的字符串。不用编号当键：
    # 编号可以为空、也可以重号，拿它当键会撞。
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
        """只改切分结果与勾选。**原文快照永远不动**——正文还要从它截。"""
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
         # 「谁写的」必须跟着落盘——丢了就成了「原文里就有」。
         "ref_from": s.ref_from, "label_from": s.label_from}
        for s in spans], ensure_ascii=False)


def _load_spans(raw: str) -> list[Span]:
    return [Span(**row) for row in json.loads(raw)]


def _dump_problems(problems: list[Problem]) -> str:
    return json.dumps([{"kind": p.kind, "detail": p.detail} for p in problems],
                      ensure_ascii=False)


def _load_problems(raw: str) -> list[Problem]:
    return [Problem(**row) for row in json.loads(raw)]
