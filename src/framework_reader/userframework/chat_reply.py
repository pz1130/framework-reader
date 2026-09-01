"""解析对话里模型的回答。

和这条线上其余每一处一样：**模型的输出是不可信输入**。它会裹围栏、
会把 `updates` 写成对象、会改一个不存在的字段。这里一条都不信。

**改一个不存在的字段是最坏的那种**——那一段内容就永远看不见了，
而页面上什么都不会报。所以字段名只认 `FIELD_LABELS` 里那七个。
"""
import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _known_fields() -> set[str]:
    from framework_reader.interpret.render import FIELD_LABELS

    return {name for name, _ in FIELD_LABELS}


def parse_reply(raw: str) -> tuple[str, list[dict], str]:
    """回 (给人看的话, 修改建议, 错误)。**从不抛异常。**"""
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
        # 形状不对就当它没提建议——`reply` 还是好的，不该整轮作废。
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
