"""厂商预设与按角色组装 client。W2 spec §3.2、§3.3

这是唯一组装 client 的地方，且每个 client 都被 GuardedClient 包住——
出网只有一条路径。
"""
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from framework_reader.llm.anthropic_adapter import AnthropicClient
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.openai_compat import OpenAICompatClient

DEFAULT_REGISTRY_PATH = Path("content/llm_providers.yaml")
KeyLookup = Callable[[str], str | None]


class MissingApiKeyError(Exception):
    """预设声明的环境变量没设。"""


class UnknownProviderError(Exception):
    """role 指向了不存在的 provider id。"""


class ProviderPreset(BaseModel):
    id: str
    kind: Literal["anthropic", "openai_compat"]
    base_url: str = ""
    api_key_env: str
    default_model: str
    explicit_cache: bool = False
    # 我们自己有没有拿真 key ping 过（`fr llm check`）。没验过的照样能用，
    # 但页面上要标出来——端点和模型名会漂，标记是给挑厂商的人看的。
    verified: bool = True
    note: str = ""


class RoleConfig(BaseModel):
    provider: str
    model: str


class LLMRegistry(BaseModel):
    providers: list[ProviderPreset]
    roles: dict[str, RoleConfig]

    @classmethod
    def load(cls, path: Path = DEFAULT_REGISTRY_PATH) -> "LLMRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            providers=[ProviderPreset(**p) for p in data.get("providers", [])],
            roles={k: RoleConfig(**v) for k, v in (data.get("roles") or {}).items()},
        )

    def preset(self, provider_id: str) -> ProviderPreset:
        for preset in self.providers:
            if preset.id == provider_id:
                return preset
        raise UnknownProviderError(f"provider not in presets: {provider_id}")

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise UnknownProviderError(f"role not configured: {name}")
        return self.roles[name]

    def build(
        self,
        role: str,
        *,
        guard: PayloadGuard,
        key_lookup: KeyLookup = os.environ.get,
    ) -> GuardedClient:
        cfg = self.role(role)
        preset = self.preset(cfg.provider)
        key = key_lookup(preset.api_key_env)
        if not key:
            raise MissingApiKeyError(
                f"Role {role} needs {preset.id}, but its key is missing: "
                f"not in the database, and environment variable {preset.api_key_env} is not set. "
                'For online deployments, have an admin fill it in on the "Models and keys" page.'
            )
        if preset.kind == "anthropic":
            inner = AnthropicClient(key, cache_system=preset.explicit_cache)
        else:
            inner = OpenAICompatClient(preset.base_url, key)
        from framework_reader.llm.retry import RetryingClient

        # 重试在 guard 里面：红线断言只跑一次，且在任何请求发出之前。
        return GuardedClient(RetryingClient(inner), guard)
