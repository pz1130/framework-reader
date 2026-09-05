"""Grounding material for copyright-restricted frameworks. Main spec §4.1, §9

For Tier B/C frameworks like ISO / PCI, the standard text is neither in the library nor ever allowed
into a model payload. The drafter gets exactly two things:

1. Our self-written Chinese titles (our own words, not the standard text)
2. The official NIST SP 800-53 mappings with their source text - 800-53 is public domain, safe to send.

Item 2 is why this path works at all: 91 of 93 ISO controls carry an official, NIST-attributed 800-53 mapping edge,
4.7 on average. What is borrowed is the **public-domain neighbour**, not the copyrighted original.
"""
import json
import re
from pathlib import Path

CATALOG_PATH = Path("vendor/nist/sp800-53r5-catalog.json")
_PARAM = re.compile(r"\s*\{\{\s*insert:[^}]*\}\}\s*")


def strip_oscal_params(text: str) -> str:
    """OSCAL's {{ insert: param, x }} placeholders only pollute the model's output."""
    return _PARAM.sub(" ", text).replace("  ", " ").strip()


def _prose_of(node: dict) -> list[str]:
    out = []
    if node.get("prose"):
        out.append(strip_oscal_params(str(node["prose"])))
    for child in node.get("parts") or []:
        out += _prose_of(child)
    return out


def catalog_prose(path: Path = CATALOG_PATH) -> dict[str, str]:
    """800-53 control id (uppercase) -> body. Reads the public-domain OSCAL catalogue in vendor."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}

    def walk(group: dict):
        for child in group.get("groups") or []:
            yield from walk(child)
        for control in group.get("controls") or []:
            yield control
            for sub in control.get("controls") or []:
                yield sub

    out: dict[str, str] = {}
    for group in data.get("catalog", {}).get("groups", []) or []:
        for control in walk(group):
            body = " ".join(p for part in control.get("parts") or [] for p in _prose_of(part))
            out[str(control["id"]).upper()] = body.strip()
    return out


def grounding_lines(
    api, control_id: str, prose: dict[str, str], *, limit: int = 6, max_chars: int = 700
) -> list[str]:
    """Grounding lines for the drafter. 800-53 neighbours only - the only public-domain tier."""
    lines: list[str] = []
    for neighbor in api.neighbors(control_id, exportable_only=True):
        if not neighbor.control_id.startswith("NIST-800-53-R5:"):
            continue
        short = neighbor.control_id.split(":", 1)[-1]
        body = prose.get(short.upper(), "")
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "..."
        lines.append(f"{short} {neighbor.label}: {body}".rstrip(": "))
        if len(lines) >= limit:
            break
    return lines
