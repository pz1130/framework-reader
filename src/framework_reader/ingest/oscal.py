"""OSCAL catalog 导入。spec §4.2①

OSCAL catalog 结构：catalog.groups[].controls[]，控制可嵌套 controls[]（enhancement）。
"""
import json
import re
from pathlib import Path

from framework_reader.schema.entities import (
    ControlStatus,
    Framework,
    FrameworkControl,
    LicenseTier,
    SupersedeRelation,
    Supersession,
    UnifiedControl,
)

_FRAMEWORK_META = {
    "NIST-800-53-R5": {
        "name": "NIST SP 800-53 Rev. 5",
        "version": "rev5",
        "source_url": "https://github.com/usnistgov/oscal-content",
        "license_note": "US Government work, public domain",
    },
    "NIST-CSF-2.0": {
        "name": "NIST Cybersecurity Framework 2.0",
        "version": "2.0",
        "source_url": (
            "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
            "nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json"
        ),
        "license_note": "US Government work, public domain",
    },
}

# 废止条目的去向。CSF 写 incorporated_into / moved_to，800-53 catalog 写连字符版本。
_SUPERSEDE_RELS = {
    "incorporated_into": SupersedeRelation.INCORPORATED_INTO,
    "moved_to": SupersedeRelation.MOVED_TO,
}

# CSF subcategory ids look like DE.CM-01; categories are DE.CM (no hyphen).
_CSF_SUBCATEGORY_RE = re.compile(r"^[A-Z]{2}\.[A-Z]{2}-\d")


def _prop_value(ctl: dict, name: str) -> str | None:
    for prop in ctl.get("props") or []:
        if prop.get("name") == name:
            return None if prop.get("value") is None else str(prop.get("value"))
    return None


def _statement_prose(ctl: dict) -> str:
    for part in ctl.get("parts") or []:
        if part.get("name") == "statement":
            return (part.get("prose") or "").strip()
    return ""


def _control_label(ctl: dict) -> str:
    cid = str(ctl.get("id") or "").strip()
    title = str(ctl.get("title") or "").strip()
    if title and title.casefold() != cid.casefold():
        return title
    return _statement_prose(ctl) or title or cid


def _control_status(ctl: dict) -> ControlStatus:
    if str(_prop_value(ctl, "status") or "").casefold() == "withdrawn":
        return ControlStatus.DEPRECATED
    return ControlStatus.ACTIVE


def _walk(node: dict, framework_id: str, parent_id: str | None,
          out: list[FrameworkControl]) -> None:
    for ctl in node.get("controls", []) or []:
        cid = f"{framework_id}:{ctl['id'].upper()}"
        out.append(
            FrameworkControl(
                id=cid,
                framework_id=framework_id,
                parent_id=parent_id,
                label=_control_label(ctl),
                label_is_original=True,   # Tier A：公共领域，可用官方标题
                framework_tier=LicenseTier.A_EMBEDDABLE,
                status=_control_status(ctl),
            )
        )
        _walk(ctl, framework_id, cid, out)


def _raw_controls(catalog: dict) -> list[dict]:
    out: list[dict] = []

    def walk(node: dict) -> None:
        for ctl in node.get("controls", []) or []:
            out.append(ctl)
            walk(ctl)
        for sub in node.get("groups", []) or []:
            walk(sub)

    for group in catalog.get("groups", []) or []:
        walk(group)
    return out


def _href_to_control_id(href: str) -> str:
    """`#ac-2_smt.k` → `AC-2`：去向可能指向语句片段，落回它所属的控制。"""
    target = href.lstrip("#").split("_smt", 1)[0]
    return target.upper()


def parse_supersessions(path: Path, framework_id: str) -> list[Supersession]:
    """解析废止条目的去向。spec §8②

    href 可能指向 function/family（如 ID.GV → GV），那些在本模型里不是控制；
    也可能指向本 catalog 之外的条目。两种都丢弃，不在图里留悬空引用。
    """
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))["catalog"]
    raw = _raw_controls(catalog)
    known = {f"{framework_id}:{c['id'].upper()}" for c in raw}

    out: list[Supersession] = []
    seen: set[tuple[str, str]] = set()
    for ctl in raw:
        if _control_status(ctl) is not ControlStatus.DEPRECATED:
            continue
        old_id = f"{framework_id}:{ctl['id'].upper()}"
        for link in ctl.get("links") or []:
            relation = _SUPERSEDE_RELS.get(str(link.get("rel") or "").replace("-", "_"))
            if relation is None:
                continue
            new_id = f"{framework_id}:{_href_to_control_id(str(link.get('href') or ''))}"
            if new_id not in known or new_id == old_id:
                continue
            if (old_id, new_id) in seen:
                continue
            seen.add((old_id, new_id))
            out.append(Supersession(old_id=old_id, new_id=new_id, relation=relation))
    return out


def unified_controls_from_csf(controls: list[FrameworkControl]) -> list[UnifiedControl]:
    """Hub rows 1:1 from active CSF 2.0 subcategories. spec §3.2①"""
    out: list[UnifiedControl] = []
    for ctl in controls:
        if ctl.framework_id != "NIST-CSF-2.0":
            continue
        if ctl.status is not ControlStatus.ACTIVE:
            continue
        local = ctl.id.split(":", 1)[-1]
        if not _CSF_SUBCATEGORY_RE.match(local):
            continue
        out.append(UnifiedControl(id=f"UC:{local}", label=ctl.label, locale="zh-CN"))
    return out


def parse_oscal_catalog(
    path: Path, framework_id: str
) -> tuple[Framework, list[FrameworkControl]]:
    meta = _FRAMEWORK_META[framework_id]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    catalog = data["catalog"]

    framework = Framework(
        id=framework_id,
        name=meta["name"],
        version=meta["version"],
        tier=LicenseTier.A_EMBEDDABLE,
        source_url=meta["source_url"],
        license_note=meta["license_note"],
    )

    controls: list[FrameworkControl] = []
    for group in catalog.get("groups", []) or []:
        _walk(group, framework_id, None, controls)
    return framework, controls
