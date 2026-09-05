"""Normalize 800-53 control IDs so mapping endpoints join OSCAL rows."""
from __future__ import annotations

import re

from framework_reader.schema.mapping import Mapping

C53_PREFIX = "NIST-800-53-R5:"
_PAREN_NUM = re.compile(r"\((\d+)\)")
_CONTROL_RE = re.compile(r"^[A-Z]{2,4}-\d")


def _strip_leading_zeros(token: str) -> str:
    if token.isdigit():
        return str(int(token))
    return token


def _local_80053_id(raw: str) -> str:
    text = (raw or "").strip()
    if text.upper().startswith(C53_PREFIX):
        text = text.split(":", 1)[1]
    return text.strip()


def normalize_80053_control_id(raw: str) -> str:
    """Map spreadsheet IDs (AC-01, AC-02(12)) onto OSCAL form (AC-1, AC-2.12)."""
    text = (raw or "").strip()
    if not text:
        return text
    prefix = ""
    if ":" in text:
        ns, rest = text.split(":", 1)
        prefix = ns + ":"
        text = rest
    text = text.strip().upper()
    text = _PAREN_NUM.sub(lambda m: f".{int(m.group(1))}", text)
    segments = [
        ".".join(_strip_leading_zeros(part) for part in seg.split("."))
        for seg in text.split("-")
    ]
    return prefix + "-".join(segments)


def looks_like_80053_control(raw: str) -> bool:
    """Family codes such as CP/IR/PT are not control endpoints."""
    local = _local_80053_id(raw).upper()
    local = _PAREN_NUM.sub(lambda m: f".{int(m.group(1))}", local)
    return bool(_CONTROL_RE.match(local))


def rewrite_mapping_80053_ids(edges: list[Mapping]) -> list[Mapping]:
    """Normalize 800-53 endpoints and drop family-only rows before insert."""
    out: list[Mapping] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        from_id, to_id = edge.from_id, edge.to_id
        drop = False
        if from_id.startswith(C53_PREFIX):
            if not looks_like_80053_control(from_id):
                drop = True
            else:
                from_id = normalize_80053_control_id(from_id)
        if to_id.startswith(C53_PREFIX):
            if not looks_like_80053_control(to_id):
                drop = True
            else:
                to_id = normalize_80053_control_id(to_id)
        if drop or from_id == to_id:
            continue
        derived = [
            normalize_80053_control_id(mid) if mid.startswith(C53_PREFIX) else mid
            for mid in edge.provenance.derived_via
        ]
        key = (from_id, to_id, edge.provenance.source)
        if key in seen:
            continue
        seen.add(key)
        if (
            from_id == edge.from_id
            and to_id == edge.to_id
            and derived == list(edge.provenance.derived_via)
        ):
            out.append(edge)
            continue
        out.append(
            edge.model_copy(
                update={
                    "from_id": from_id,
                    "to_id": to_id,
                    "provenance": edge.provenance.model_copy(
                        update={"derived_via": derived}
                    ),
                }
            )
        )
    return out
