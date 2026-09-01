"""适用性声明（SoA）。主 spec §7.3.3

SoA 是 ISO 27001 认证要交的文件。两条硬规则：

1. **93 条一条都不能少。** 没填的标成「待填」摆在表里，不能悄悄消失——
   漏掉的行不是「没问题」，是「没人看过」。
2. **推导边绝不进这张表。** 它们准确率 17%，只配在录入时当填写提示。§3.3
"""
import csv
import io

from pydantic import BaseModel

from framework_reader.assess.store import Assessment

HEADERS = ("Control", "Name", "Applicability", "Reason if N/A", "Implementation status", "Notes / evidence")
PENDING = "TBD"


class SoaRow(BaseModel):
    control_id: str
    label: str
    applicable: bool | None = None      # None = 还没评
    reason: str = ""
    status: str = ""
    note: str = ""


def build_soa(
    controls: list[tuple[str, str]], entries: list[Assessment]
) -> list[SoaRow]:
    seen = {e.control_id: e for e in entries}
    rows = []
    for control_id, label in controls:
        entry = seen.get(control_id)
        if entry is None:
            rows.append(SoaRow(control_id=control_id, label=label))
            continue
        rows.append(SoaRow(
            control_id=control_id, label=label,
            applicable=entry.applicable, reason=entry.reason,
            status=entry.status, note=entry.note,
        ))
    return rows


def _cells(row: SoaRow) -> tuple[str, ...]:
    if row.applicable is None:
        applicable = PENDING
    else:
        applicable = "Applicable" if row.applicable else "Not applicable"
    return (
        row.control_id.split(":", 1)[-1],
        row.label,
        applicable,
        row.reason,
        row.status or (PENDING if row.applicable else ""),
        row.note,
    )


def render_soa_markdown(rows: list[SoaRow]) -> str:
    def escape(text: str) -> str:
        # 竖线不转义会把这一行的列数撑开，整张表错位。
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(HEADERS) + " |",
        "|" + "|".join(["---"] * len(HEADERS)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape(c) for c in _cells(row)) + " |")
    return "\n".join(lines)


def render_soa_csv(rows: list[SoaRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(_cells(row))
    return buffer.getvalue()


def fill_hints(api, control_id: str, limit: int = 4) -> list[str]:
    """录入 SoA 时的填写提示：这条和别的框架的哪些条款对得上。

    **只在录入时出现，绝不进交付物。** 官方映射（L1）与推导边（L2）分开标——
    推导边 R7 抽样 correct 17%，给人回忆用可以，当依据不行。主 spec §3.3

    同标题只留一条：800-53 每个族都有一条 `-1 Policy and Procedures`，
    不去重的话四条提示会是同一句话。
    """
    neighbors = sorted(api.neighbors(control_id), key=lambda n: not n.exportable)
    seen: set[str] = set()
    hints: list[str] = []
    for neighbor in neighbors:
        if neighbor.label in seen:
            continue
        seen.add(neighbor.label)
        kind = "Mapped (official mapping)" if neighbor.exportable else "Hint (inferred, not citable)"
        hints.append(f"{kind}: {neighbor.control_id}  {neighbor.label}")
        if len(hints) >= limit:
            break
    return hints
