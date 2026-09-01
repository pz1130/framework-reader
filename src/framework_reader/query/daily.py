"""每天三条带解读的条款。种子是日期，同一天打开是同一组。

「换一批」往种子里掺一个批次号（?roll=N）：同一个批次当天稳定、可书签，
点一下换一组——随机性在换的那一下，不在每次刷新。
"""
import random
from datetime import date

from framework_reader.query.api import QueryAPI


def daily_controls(api: QueryAPI, *, today: date, n: int = 3,
                   roll: int = 0) -> list[dict]:
    pool = api.list_interpreted(leaf_only=True)
    if not pool:
        return []
    if len(pool) > n:
        pool = random.Random(_seed(today, roll)).sample(pool, n)
    names = {}
    return [_card(api, control, names) for control in pool]


def _seed(today: date, roll: int) -> str:
    """默认按日期当种子（老契约：同一天同一组，roll=0 保持原种子不变）。
    roll ≥ 1 是「换一批」的批次号。"""
    return today.isoformat() if roll <= 0 else f"{today.isoformat()}#{roll}"


def _card(api: QueryAPI, control, names: dict) -> dict:
    interp = api.interpretation(control.id)
    snippet = ""
    for field in ("plain_zh", "intent"):
        value = (interp.get(field) or {}).get("value")
        if isinstance(value, str) and value.strip():
            snippet = value.strip()
            break
    if len(snippet) > 90:
        snippet = snippet[:89] + "…"
    if control.framework_id not in names:
        view = api.get_framework(control.framework_id)
        names[control.framework_id] = view.name if view else control.framework_id
    return {
        "id": control.id,
        "short": control.id.split(":", 1)[-1],
        "label": control.label,
        "snippet": snippet,
        "framework": names[control.framework_id],
    }
