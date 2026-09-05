"""Collect the model's expanded words and control ids into a searchable list.

Model output is untrusted input: it wraps fences, turns terms into one sentence, invents ids.
Nothing here is trusted. Invented ids simply fall through the database search - this module only
"""
import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_MAX = 8


def parse_expansion(raw: str) -> tuple[list[str], list[str], str]:
    """Returns (expanded words, ids, error). **Never raises.**"""
    text = _FENCE.sub("", (raw or "").strip())
    if not text:
        return [], [], "The model returned nothing."
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [], [], "The model did not reply in the expected format (not JSON)."
    if not isinstance(payload, dict):
        return [], [], "The model reply is not an object."
    terms = _strings(payload.get("terms"))
    ids = _strings(payload.get("ids"))
    return terms, ids, ""


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
        if len(out) >= _MAX:
            break
    return out


def hits_for(api, terms: list[str], ids: list[str], *, limit: int = 20):
    """Search the graph with the expanded words and ids. Ids absent from the library just disappear."""
    seen: set[str] = set()
    out = []

    def take(hit) -> None:
        if hit.id in seen:
            return
        seen.add(hit.id)
        out.append(hit)

    for control_id in ids:
        found = api.get_control(control_id)
        if found is not None:
            take(found)
            continue
        for hit in api.search(control_id, limit=limit):
            take(hit)
            if len(out) >= limit:
                return out
    for term in terms:
        for hit in api.search(term, limit=limit):
            take(hit)
            if len(out) >= limit:
                return out
    return out[:limit]
