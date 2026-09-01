"""把 106 条中文解读做成可发布的一页。主 spec §7.3.4

发布的是**我们自己写的解读**，不是标准原文。CSF 2.0 是 NIST 公共领域，
条款编号与英文标题可以照登；ISO / PCI 那类受版权的东西一个字都不在这里。

只渲**可导出**的映射边。L2 推导边 R7 抽样 correct 17%，进不了任何导出物。
"""
from html import escape

from pydantic import BaseModel

FIELD_LABELS = (
    ("intent", "What it defends against"),
    ("plain_zh", "Plain words"),
    ("practice", "How to implement"),
    ("evidence", "What serves as evidence"),
    ("common_myth", "Common misconceptions"),
    ("auditor_asks", "What auditors will probe"),
    ("regional_note", "Regional notes"),
)

# CSF 自己的顺序，不是字母序——按字母排 DE 会跑到 GV 前面。
FUNCTION_ORDER = ("GV", "ID", "PR", "DE", "RS", "RC")

FUNCTION_NAMES = {
    "GV": "Govern", "ID": "Identify", "PR": "Protect",
    "DE": "Detect", "RS": "Respond", "RC": "Recover",
}

# 每个框架的分组规则不一样：CSF 按 function（DE.CM-01 → DE），
# ISO 按主题（A.5.1 → A.5）——按点切第一段会把 93 条全挤成「A」一组。
FRAMEWORKS: dict[str, dict] = {
    "NIST-CSF-2.0": {
        "name": "NIST CSF 2.0",
        "note": "Published by NIST, public domain. Control numbers and English titles reproduced as-is.",
        "group": lambda local: local.split(".", 1)[0],
        "order": FUNCTION_ORDER,
        "names": FUNCTION_NAMES,
    },
    "ISO-27002-2022": {
        "name": "ISO/IEC 27002:2022",
        "note": "A copyrighted standard that must be purchased. This page does not reproduce a single word of its text. "
                "The titles after the numbers are our own wording; interpretations are inferred from mapped public-domain materials.",
        "group": lambda local: local.rsplit(".", 1)[0],
        "order": ("A.5", "A.6", "A.7", "A.8"),
        "names": {"A.5": "Organizational", "A.6": "People", "A.7": "Physical", "A.8": "Technological"},
    },
}


class MappingOut(BaseModel):
    control_id: str
    label: str
    source: str


class Entry(BaseModel):
    control_id: str
    short_id: str
    framework: str
    group: str
    label: str
    fields: list[tuple[str, object]]
    mappings: list[MappingOut]


def collect(api, framework_id: str = "NIST-CSF-2.0") -> list[Entry]:
    config = FRAMEWORKS.get(framework_id, FRAMEWORKS["NIST-CSF-2.0"])
    entries: list[Entry] = []
    for ctl in api.list_controls(framework_id, leaf_only=True):
        raw = api.interpretation(ctl.id)
        if not raw:
            continue
        fields = []
        for name, _label in FIELD_LABELS:
            value = (raw.get(name) or {}).get("value")
            if value in (None, "", [], {}):
                continue          # 空字段直接不出现，不渲染成 null
            fields.append((name, value))
        short_id = ctl.id.split(":", 1)[-1]
        entries.append(Entry(
            control_id=ctl.id,
            short_id=short_id,
            framework=framework_id,
            group=config["group"](short_id),
            label=ctl.label,
            fields=fields,
            mappings=[
                MappingOut(control_id=n.control_id, label=n.label, source=n.source)
                for n in api.neighbors(ctl.id, exportable_only=True)
            ],
        ))
    order = {name: index for index, name in enumerate(config["order"])}
    entries.sort(key=lambda e: (order.get(e.group, len(order)), e.short_id))
    return entries


def _render_value(value: object) -> str:
    if isinstance(value, dict):
        items = "".join(
            f'<li><span class="rung">Level {escape(str(k))}</span>{escape(str(v))}</li>'
            for k, v in sorted(value.items())
        )
        return f'<ol class="rungs">{items}</ol>'
    if isinstance(value, list):
        items = "".join(f"<li>{escape(str(v))}</li>" for v in value)
        return f"<ul>{items}</ul>"
    return f"<p>{escape(str(value))}</p>"


def _render_entry(entry: Entry) -> str:
    labels = dict(FIELD_LABELS)
    blocks = []
    for name, value in entry.fields:
        mark = ' data-ask="1"' if name == "auditor_asks" else ""
        blocks.append(
            f'<div class="field"{mark}><h4>{escape(labels[name])}</h4>'
            f"{_render_value(value)}</div>"
        )
    if entry.mappings:
        sources = sorted({m.source for m in entry.mappings})
        items = "".join(
            f'<li><code>{escape(m.control_id.split(":", 1)[-1])}</code> {escape(m.label)}</li>'
            for m in entry.mappings
        )
        blocks.append(
            '<div class="field mapping"><h4>Mappings to NIST SP 800-53 Rev.5</h4>'
            f"<ul>{items}</ul>"
            f'<p class="src">Sources: {escape(", ".join(sources))} (official mappings, traceable line by line)</p>'
            "</div>"
        )
    return (
        f'<article class="ctl" id="{escape(entry.short_id)}" '
        f'data-fw="{escape(entry.framework)}" data-fn="{escape(entry.group)}" '
        f'data-q="{escape(entry.short_id + " " + entry.label)}">'
        f'<header><code class="cid">{escape(entry.short_id)}</code>'
        f'<span class="en">{escape(entry.label)}</span></header>'
        + "".join(blocks) + "</article>"
    )


def _chips(framework_id: str, entries: list[Entry]) -> str:
    config = FRAMEWORKS.get(framework_id, FRAMEWORKS["NIST-CSF-2.0"])
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.group] = counts.get(entry.group, 0) + 1
    return "".join(
        f'<button class="chip" data-fn="{escape(group)}">'
        f'{escape(config["names"].get(group, group))} <span>{counts[group]}</span></button>'
        for group in config["order"] if group in counts
    )


def render_page(entries: list[Entry]) -> str:
    """单框架页。多框架用 render_multi。"""
    framework_id = entries[0].framework if entries else "NIST-CSF-2.0"
    return render_multi([(framework_id, entries)])


def render_multi(groups: list[tuple[str, list[Entry]]]) -> str:
    from framework_reader.publish.template import PAGE

    tabs, chipsets, bodies, notes = [], [], [], []
    total = sum(len(entries) for _fw, entries in groups)
    for index, (framework_id, entries) in enumerate(groups):
        config = FRAMEWORKS.get(framework_id, FRAMEWORKS["NIST-CSF-2.0"])
        active = ' aria-pressed="true"' if index == 0 else ' aria-pressed="false"'
        tabs.append(
            f'<button class="tab" data-fw="{escape(framework_id)}"{active}>'
            f'{escape(config["name"])} <span>{len(entries)}</span></button>'
        )
        chipsets.append(
            f'<div class="chips" data-fw="{escape(framework_id)}"'
            f'{"" if index == 0 else " hidden"}>{_chips(framework_id, entries)}</div>'
        )
        notes.append(
            f'<p class="fwnote" data-fw="{escape(framework_id)}"'
            f'{"" if index == 0 else " hidden"}>'
            f'<strong>{escape(config["name"])}</strong>: {escape(config["note"])}</p>'
        )
        bodies.append("".join(_render_entry(e) for e in entries))

    return (
        PAGE.replace("<!--TOTAL-->", str(total))
        .replace("<!--TABS-->", "".join(tabs))
        .replace("<!--CHIPS-->", "".join(chipsets))
        .replace("<!--NOTES-->", "".join(notes))
        .replace("<!--ENTRIES-->", "".join(bodies))
    )
