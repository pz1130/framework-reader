"""盲测抽样。spec §3

按 CSF 六个 function 分层：纯随机可能抽出一堆治理条款，分层保证覆盖面。
配额按最大余数法从实际构成算出，不写死——106 条的构成若变，配额自动跟着变。
"""
import hashlib
import random
from collections.abc import Iterable

from framework_reader.blindtest.packet import AnswerKey
from framework_reader.blindtest.variants import leak_hits


def function_of(control_id: str) -> str:
    """`NIST-CSF-2.0:DE.CM-01` → `DE`"""
    local = control_id.split(":", 1)[-1]
    return local.split(".", 1)[0]


def quotas(counts: dict[str, int], total: int) -> dict[str, int]:
    """最大余数法：先取整数部分，余下的名额按小数部分从大到小分配。"""
    population = sum(counts.values())
    if population == 0:
        return {}
    exact = {name: count * total / population for name, count in counts.items()}
    out = {name: int(value) for name, value in exact.items()}
    remaining = total - sum(out.values())
    order = sorted(
        counts, key=lambda name: (exact[name] - int(exact[name]), counts[name], name),
        reverse=True,
    )
    for name in order[:remaining]:
        out[name] += 1
    return out


def eligible_for_sample(
    control_ids: list[str], outcomes: dict[str, str]
) -> list[str]:
    """原文含泄露词的条款不能进抽样框，否则 build_packet 会拒出题。spec §4"""
    return [cid for cid in control_ids if not leak_hits(outcomes.get(cid, ""))]


def stratified_sample(control_ids: list[str], n: int, seed: int) -> list[str]:
    if len(control_ids) < n:
        raise ValueError(f"Not enough to sample from: only {len(control_ids)} control(s), need {n}")

    buckets: dict[str, list[str]] = {}
    for control_id in sorted(control_ids):
        buckets.setdefault(function_of(control_id), []).append(control_id)

    counts = {name: len(items) for name, items in buckets.items()}
    plan = quotas(counts, n)

    rng = random.Random(seed)
    picked: list[str] = []
    for name in sorted(plan):
        take = min(plan[name], len(buckets[name]))
        picked += rng.sample(buckets[name], take)
    return sorted(picked)


def frame_fingerprint(control_ids: Iterable[str]) -> str:
    """抽样框的指纹。

    seed 单独一个数字保证不了复现——同一个 seed 配上不同的抽样框会抽出另一批题。
    spec §3 记 seed 是为了防止「结果难看再换一批重抽」，那道防线要靠这个指纹才站得住。
    """
    joined = "\n".join(sorted(set(control_ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def frame_drift(key: AnswerKey, control_ids: Iterable[str]) -> str | None:
    """既有 answer_key 与当前抽样框对不上时给出说明，对得上返回 None。"""
    now = frame_fingerprint(control_ids)
    if not key.frame_fingerprint:
        return (
            f"seed={key.seed}'s answer_key predates the frame fingerprint; cannot verify whether it reproduces"
            f" (current frame fingerprint {now})"
        )
    if key.frame_fingerprint != now:
        return (
            f"Sampling frame changed: at prepare time {key.frame_fingerprint}, now {now}. "
            f"The same seed={key.seed} no longer reproduces the original set of items"
        )
    return None
