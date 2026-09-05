"""Three interpreted controls a day. The seed is the date: same day, same three.

"Shuffle" mixes a roll number into the seed (?roll=N): one batch is stable within the day and
bookmarkable; one click swaps the set - the randomness is in the shuffle, not every refresh.
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
    """Default seed is the date (old contract: same day same set; roll=0 keeps the original seed).
    roll >= 1 is a "shuffle" batch number."""
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
