"""差距报告。主 spec §7.3.3

「下一步做什么」**不需要任何推理**——`practice` 本来就是 1/2/3 三档写好的，
你在 1 档，下一步就是 2 档那段话。这里唯一做的事是查表、排序、把证据带上。
"""
from pydantic import BaseModel

from framework_reader.assess.store import Assessment

MAX_LEVEL = 3


class GapItem(BaseModel):
    control_id: str
    label: str
    level: int
    next_step: str
    evidence: str
    note: str


class GapReport(BaseModel):
    assessed: int
    total: int
    not_applicable: int
    at_top: int
    by_level: dict[int, int]
    items: list[GapItem]


def build_gap(
    entries: list[Assessment], content: dict[str, dict], total: int
) -> GapReport:
    items: list[GapItem] = []
    by_level: dict[int, int] = {}
    not_applicable = 0
    at_top = 0

    for entry in entries:
        if not entry.applicable:
            not_applicable += 1
            continue
        if entry.level is None:
            continue
        by_level[entry.level] = by_level.get(entry.level, 0) + 1
        if entry.level >= MAX_LEVEL:
            at_top += 1
            continue
        info = content.get(entry.control_id, {})
        practice = info.get("practice") or {}
        items.append(GapItem(
            control_id=entry.control_id,
            label=info.get("label", ""),
            level=entry.level,
            # 下一档的原话。查不到（如 ISO 那 93 条还没有解读）就留空，
            # 但这条控制仍要出现在报告里——没解读不等于没差距。
            next_step=str(practice.get(str(entry.level + 1), "")),
            evidence=str(info.get("evidence") or ""),
            note=entry.note,
        ))

    items.sort(key=lambda i: (i.level, i.control_id))
    return GapReport(
        assessed=len(entries), total=total, not_applicable=not_applicable,
        at_top=at_top, by_level=by_level, items=items,
    )


def render_gap(report: GapReport) -> str:
    if not report.assessed:
        return "No self-assessment yet. Run `fr assess` to record where you stand."

    spread = "  ".join(
        f"{level} at L{report.by_level[level]}" for level in sorted(report.by_level)
    )
    lines = [
        f"Assessed  {report.assessed}/{report.total} controls",
        f"Level spread  {spread}" if spread else "Level spread  (none)",
    ]
    if report.not_applicable:
        lines.append(f"Not applicable  {report.not_applicable} controls")
    if report.at_top:
        lines.append(f"Already at L3  {report.at_top} controls")

    if not report.items:
        lines += ["", "No improvement gaps among the assessed controls."]
        return "\n".join(lines)

    lines += ["", "Weakest first:"]
    for item in report.items:
        label = f"  {item.label}" if item.label else ""
        lines.append("")
        lines.append(f"[L{item.level}] {item.control_id}{label}")
        if item.note:
            lines.append(f"  Current: {item.note}")
        if item.next_step:
            lines.append(f"  Next step (to L{item.level + 1}): {item.next_step}")
        else:
            lines.append("  Next step: this control has no interpretation yet - judge for yourself")
        if item.evidence:
            lines.append(f"  Evidence: {item.evidence}")
    return "\n".join(lines)
