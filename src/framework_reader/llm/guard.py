"""Egress red line: Tier C/D original text must not enter the payload of any model call. W2 spec §3.4③

Same level as the two red lines in main spec §10.A: raise, no retry, no fallback, no
downgrade to warn.
"""
import sqlite3
from collections.abc import Sequence

from framework_reader.llm.client import LLMClient, Message


class OutboundTextError(Exception):
    """Copyrighted original text was about to leave the perimeter. Build/run must abort."""


class PayloadGuard:
    def __init__(self, forbidden: Sequence[str], min_chunk: int = 24) -> None:
        # Short fragments ("the organization shall define" and the like) collide in any
        # compliance text; only matching whole passages is meaningful.
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
    """The only egress path. Every client assembled by the registry is wrapped in it."""

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
