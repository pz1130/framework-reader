"""Blind-test sampling. spec §3

Stratified by the six CSF functions: pure random could draw a pile of governance
controls; stratification guarantees coverage. Quotas are computed from the actual
composition by the largest-remainder method and are not hard-coded — if the composition
of the 106 controls changes, the quotas follow automatically.
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
    """Largest-remainder method: take the integer parts first, then assign the remaining
    seats by fractional part, largest first."""
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
    """Controls whose original text contains leak words must stay out of the sampling
    frame, otherwise build_packet refuses to produce the questions. spec §4"""
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
    """Fingerprint of the sampling frame.

    The seed alone cannot guarantee reproducibility — the same seed over a different
    frame samples a different batch of questions. spec §3 records the seed to prevent
    "re-sampling a new batch when the results look bad"; that line of defense only holds
    up with this fingerprint behind it.
    """
    joined = "\n".join(sorted(set(control_ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def frame_drift(key: AnswerKey, control_ids: Iterable[str]) -> str | None:
    """Explains when the existing answer_key does not match the current sampling frame;
    returns None when it does."""
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
