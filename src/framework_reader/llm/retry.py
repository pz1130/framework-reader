"""退避重试。W2 spec §6

红线异常不重试——那不是瞬时故障，是设计违规。
"""
import time
from collections.abc import Callable

from framework_reader.llm.client import LLMClient, Message
from framework_reader.llm.guard import OutboundTextError


class RetryingClient:
    def __init__(
        self,
        inner: LLMClient,
        *,
        attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base_delay = base_delay
        self._sleep = sleep

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                return self._inner.complete(
                    system, messages, model=model, max_tokens=max_tokens,
                    response_format=response_format)
            except OutboundTextError:
                raise                      # 红线不是瞬时故障，一次也不重试
            except Exception as exc:
                last = exc
                if attempt < self._attempts - 1:
                    self._sleep(self._base_delay * (2**attempt))
        assert last is not None
        raise last
