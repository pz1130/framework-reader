"""Terminology glossary and consistency checking. spec §4.2④, §10.B3"""
from pathlib import Path

import yaml
from pydantic import BaseModel


class GlossaryEntry(BaseModel):
    preferred: str
    banned: list[str] = []
    en: str = ""
    rationale: str


class Glossary(BaseModel):
    entries: list[GlossaryEntry]

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(entries=[GlossaryEntry(**e) for e in data.get("terms", [])])

    def check_text(self, text: str) -> list[str]:
        hits: list[str] = []
        for entry in self.entries:
            for bad in entry.banned:
                if bad in text:
                    hits.append(bad)
        return hits

    def check_file(self, path: Path) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for lineno, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), start=1
        ):
            hits = self.check_text(line)
            if hits:
                out[lineno] = hits
        return out
