"""把模型扩出来的词和条号收成可拿去搜的清单。

模型的输出是不可信输入：会裹围栏、会把 terms 写成一句话、会编造条号。
这里一条都不信。编造的条号在搜库时自然掉下去——本模块只负责剥出字符串。
"""
import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_MAX = 8


def parse_expansion(raw: str) -> tuple[list[str], list[str], str]:
    """回 (扩出来的词, 条号, 错误)。**从不抛异常。**"""
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
    """拿扩出来的词和条号去图谱里搜。库里没有的条号就此消失。"""
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
