"""Model client protocol. W2 spec §3.1

All adapters implement the same complete(); the caller never touches a vendor SDK
directly.
"""
import threading
from typing import Literal, Protocol

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str: ...


class FakeClient:
    """For tests. Public CI is zero-network; every model call goes through it. W2 spec §7"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        with self._lock:
            self.calls.append({
                "system": system,
                "messages": [m.model_dump() for m in messages],
                "model": model,
                "max_tokens": max_tokens,
                "response_format": response_format,
            })
            assert self._responses, "FakeClient preset responses exhausted - test fixture and code under test disagree"
            return self._responses.pop(0)
