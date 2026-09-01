"""受版权框架的接地材料。主 spec §4.1、§9

ISO / PCI 这类 Tier B/C 框架，条款原文既不在库里，也**永远不许进模型调用的
payload**。给起草器的东西只有两样：

1. 我们自写的中文标题（自己的文字，不是标准原文）
2. 官方映射到的 NIST SP 800-53 控制及其原文——800-53 是公共领域，可以发

第 2 条是这条路走得通的原因：91/93 条 ISO 控制有 NIST 官方署名的 800-53 映射边，
平均 4.7 条。借的是**公共领域的邻居**，不是受版权的本体。
"""
import json
import re
from pathlib import Path

CATALOG_PATH = Path("vendor/nist/sp800-53r5-catalog.json")
_PARAM = re.compile(r"\s*\{\{\s*insert:[^}]*\}\}\s*")


def strip_oscal_params(text: str) -> str:
    """OSCAL 的 `{{ insert: param, x }}` 占位符发给模型只会污染输出。"""
    return _PARAM.sub(" ", text).replace("  ", " ").strip()


def _prose_of(node: dict) -> list[str]:
    out = []
    if node.get("prose"):
        out.append(strip_oscal_params(str(node["prose"])))
    for child in node.get("parts") or []:
        out += _prose_of(child)
    return out


def catalog_prose(path: Path = CATALOG_PATH) -> dict[str, str]:
    """800-53 控制号（大写）→ 正文。读 vendor 里的公共领域 OSCAL 目录。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}

    def walk(group: dict):
        for child in group.get("groups") or []:
            yield from walk(child)
        for control in group.get("controls") or []:
            yield control
            for sub in control.get("controls") or []:
                yield sub

    out: dict[str, str] = {}
    for group in data.get("catalog", {}).get("groups", []) or []:
        for control in walk(group):
            body = " ".join(p for part in control.get("parts") or [] for p in _prose_of(part))
            out[str(control["id"]).upper()] = body.strip()
    return out


def grounding_lines(
    api, control_id: str, prose: dict[str, str], *, limit: int = 6, max_chars: int = 700
) -> list[str]:
    """给起草器的接地行。只取 800-53 邻居——只有它是公共领域。"""
    lines: list[str] = []
    for neighbor in api.neighbors(control_id, exportable_only=True):
        if not neighbor.control_id.startswith("NIST-800-53-R5:"):
            continue
        short = neighbor.control_id.split(":", 1)[-1]
        body = prose.get(short.upper(), "")
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "..."
        lines.append(f"{short} {neighbor.label}: {body}".rstrip(": "))
        if len(lines) >= limit:
            break
    return lines
