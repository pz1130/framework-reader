"""The source-authorisation allowlist. spec §4.3, §10.A

Registered per "NIST-attributed file", not per site.
"""
from pathlib import Path

import yaml
from pydantic import BaseModel


class DisallowedSourceError(Exception):
    """provenance.source is not on the allowlist."""


class SourceEntry(BaseModel):
    id: str
    license: str
    url: str = ""
    checked_on: str
    note: str = ""


class DeniedEntry(BaseModel):
    id: str
    reason: str
    checked_on: str


class SourceRegistry(BaseModel):
    entries: list[SourceEntry]
    denied: list[DeniedEntry]

    @classmethod
    def load(cls, path: Path) -> "SourceRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

        def coerce(entry: dict) -> dict:
            out = dict(entry)
            v = out.get("checked_on")
            if hasattr(v, "isoformat"):
                out["checked_on"] = v.isoformat()
            return out

        return cls(
            entries=[SourceEntry(**coerce(e)) for e in data.get("allowed", [])],
            denied=[DeniedEntry(**coerce(e)) for e in data.get("denied", [])],
        )

    def is_allowed(self, source: str) -> bool:
        return any(e.id == source for e in self.entries)

    def assert_allowed(self, source: str) -> None:
        if self.is_allowed(source):
            return
        for d in self.denied:
            if source.startswith(d.id):
                raise DisallowedSourceError(f"Source {source} is explicitly banned: {d.reason}")
        raise DisallowedSourceError(
            f"Source {source} is not on the allowlist. The allowlist is registered per NIST-attributed file; "
            f"a domain by itself is not grounds for allowance. If you truly need it, first record the authorization and verification date in "
            f"content/allowed_sources.yaml."
        )
