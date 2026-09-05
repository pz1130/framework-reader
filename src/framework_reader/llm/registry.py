"""Vendor presets and per-role client assembly. W2 spec §3.2, §3.3

This is the only place that assembles a client, and every client is wrapped in a
GuardedClient — egress has exactly one path.
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
    """The environment variable a preset declares is not set."""


class UnknownProviderError(Exception):
    """A role points at a provider id that does not exist."""


class ProviderPreset(BaseModel):
    id: str
    kind: Literal["anthropic", "openai_compat"]
    base_url: str = ""
    api_key_env: str
    default_model: str
    explicit_cache: bool = False
    # Whether we ourselves have pinged it with a real key (`fr llm check`). Unverified
    # presets still work, but they must be flagged on the page — endpoints and model
    # names drift, and the flag is for whoever is picking a vendor.
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

        # Retry inside the guard: the red-line assertion runs exactly once, and before
        # any request goes out.
        return GuardedClient(RetryingClient(inner), guard)
