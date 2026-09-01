"""一条解读读起来什么样。

这里是**唯一**一处决定解读怎么呈现给人看的代码——`fr show` 和盲测的产品变体
共用它。分成两处写，两处就会慢慢长得不一样。

原先住在 `blindtest/variants.py`。2026-08-22 自用降级后盲测不再是关卡，
主查询路径不该反过来依赖它。主 spec §7.3.1
"""
from pydantic import BaseModel

from framework_reader.interpret.model import Interpretation


FIELD_LABELS = (
    ("intent", "What it defends against"),
    ("plain_zh", "Plain words"),
    ("practice", "How to implement"),
    ("evidence", "What serves as evidence"),
    ("common_myth", "Common misconceptions"),
    ("auditor_asks", "What auditors will probe"),
    ("regional_note", "Regional notes"),
)

_MAPPING_LABEL = "Mappings to other frameworks"


# 关系与等级都翻成中文再给评委看。原样打印 `related` / `L1_OFFICIAL`
# 是把内部枚举名甩在读者脸上，且「related」本身也不该出现在中文材料里。
_RELATION_LABELS = {
    "equivalent": "Equivalent",
    "subset": "Covered by",
    "superset": "Covers",
    "related": "Related",
    "conflicts": "Conflicts",
}
_LEVEL_LABELS = {
    "L1_OFFICIAL": "Official mapping",
    "L2_PUBLIC": "Published crosswalk",
    "L3_CONFIRMED": "Human confirmed",
}


class MappingRef(BaseModel):
    """一条可导出的映射边，已经带上展示所需的全部字段。

    由调用方从 QueryAPI 组装——variants 不碰数据库，才能脱库测。
    """

    control_id: str
    label: str
    framework: str
    relation: str
    source: str
    level: str


def _short_id(control_id: str) -> str:
    """`NIST-800-53-R5:AC-4` → `AC-4`。框架名已经写在组标题上了。"""
    return control_id.split(":", 1)[-1]


def render_mappings(refs: list[MappingRef]) -> str:
    """这条对应到哪些别的框架条款，以及对应关系的出处。主 spec §7.3 第二条通过线

    出处指的是**映射**的出处，不是字段的——spec §4 把 basis / inferred 列为
    泄露词，字段级依据本来就不许出现在 packet 里。
    """
    if not refs:
        return ""

    groups: dict[tuple[str, str], list[MappingRef]] = {}
    for ref in refs:
        groups.setdefault((ref.framework, ref.relation), []).append(ref)

    lines = [f"**{_MAPPING_LABEL}**"]
    for (framework, relation), items in groups.items():
        shown = ", ".join(f"{_short_id(i.control_id)} {i.label}" for i in items)
        relation_zh = _RELATION_LABELS.get(relation, relation)
        lines.append(f"- {framework} ({relation_zh}, {len(items)} controls): {shown}")

    sources = dict.fromkeys(
        f"{ref.source} ({_LEVEL_LABELS.get(ref.level, ref.level)})" for ref in refs
    )
    lines.append(f"- Mapping sources: {'; '.join(sources)}, each traceable")
    return "\n".join(lines)


def render_interpretation(
    interp: Interpretation, mappings: list[MappingRef] | None = None
) -> str:
    lines: list[str] = []
    for name, label in FIELD_LABELS:
        field = interp.fields.get(name)
        if field is None or field.value in (None, "", [], {}):
            continue          # 留空的字段直接不出现，不显示 None/null
        lines.append(f"**{label}**")
        value = field.value
        if isinstance(value, dict):
            for level, body in sorted(value.items()):
                lines.append(f"- {level} - {body}")
        elif isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        else:
            lines.append(str(value))
        lines.append("")

    block = render_mappings(mappings or [])
    if block:
        lines.append(block)
    return "\n".join(lines).strip()
