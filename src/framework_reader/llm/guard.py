"""出口红线：Tier C/D 原文不得进入任何模型调用的 payload。W2 spec §3.4③

与主 spec §10.A 的两条红线同级：抛异常，不重试、不降级、不降为 warn。
"""
import sqlite3
from collections.abc import Sequence

from framework_reader.llm.client import LLMClient, Message


class OutboundTextError(Exception):
    """受版权原文即将出圈。构建/运行必须中止。"""


class PayloadGuard:
    def __init__(self, forbidden: Sequence[str], min_chunk: int = 24) -> None:
        # 短片段（「组织应定义」之类）在任何中文文本里都会撞上，按整段比对才有意义。
        self._forbidden = [t.strip() for t in forbidden if len(t.strip()) >= min_chunk]

    def check(self, *texts: str) -> None:
        for text in texts:
            for body in self._forbidden:
                if body in text:
                    raise OutboundTextError(
                        f"payload contains copyrighted original text (first 20 chars: {body[:20]}...) - "
                        f"Tier C/D original text must not be sent to any model vendor"
                    )


def forbidden_texts_from_db(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT body FROM original_text").fetchall()]


class GuardedClient:
    """唯一的出网路径。registry 组装的每个 client 都被它包住。"""

    def __init__(self, inner: LLMClient, guard: PayloadGuard) -> None:
        self._inner = inner
        self._guard = guard

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        self._guard.check(system, *(m.content for m in messages))
        return self._inner.complete(
            system, messages, model=model, max_tokens=max_tokens,
            response_format=response_format)
