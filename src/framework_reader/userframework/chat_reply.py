"""Parse the model's reply in the conversation.

Like every other place on this pipeline: **the model's output is untrusted
input**. It will wrap things in fences, write `updates` as an object, or edit a
field that does not exist. Not one word of it is trusted here.

**Editing a nonexistent field is the worst kind** - that content becomes
invisible forever, and the page reports nothing. So the only field names
accepted are the seven in `FIELD_LABELS`.
"""
import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _known_fields() -> set[str]:
    from framework_reader.interpret.render import FIELD_LABELS

    return {name for name, _ in FIELD_LABELS}


def parse_reply(raw: str) -> tuple[str, list[dict], str]:
    """Returns (human-readable reply, edit suggestions, error). **Never raises.**"""
    text = _FENCE.sub("", (raw or "").strip())
    if not text:
        return "", [], "The model returned nothing. Ask again."
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "", [], "The model reply is not JSON. Ask again."
    if not isinstance(payload, dict):
        return "", [], "The model reply is not an object. Ask again."

    reply = payload.get("reply")
    reply = "" if reply is None else str(reply)
    raw_updates = payload.get("updates")
    if not isinstance(raw_updates, list):
        # Wrong shape means it offered no suggestions - `reply` is still good and
        # the whole turn should not be voided.
        return reply, [], ""

    known = _known_fields()
    updates = []
    for row in raw_updates:
        if not isinstance(row, dict):
            continue
        name = str(row.get("field", "")).strip()
        if name not in known or "value" not in row:
            continue
        updates.append({"field": name, "value": row["value"]})
    return reply, updates, ""
