"""起草器：四个非差异化字段。W2 spec §2 表格第一行

三个差异化字段哪怕模型主动给了也丢弃——D1 的第一道闸。
"""
import json
from pathlib import Path

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import PROMPT_VERSIONS, load_prompt

__all__ = [
    "DrafterOutputError", "draft_fields", "draft_full_fields", "PROMPT_VERSIONS",
]


DRAFT_FAILURE_DIR = Path("build/draft_failures")


class DrafterOutputError(Exception):
    """模型输出不符合约定结构。不猜、不修，直接失败。"""


_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _repair_json(text: str) -> str:
    """只修两类语法毛病：字符串内的裸控制字符、结构里的尾逗号。

    逐字符扫描并跟踪是否身处字符串，因此字符串里的逗号、括号一律不动。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in _ESCAPES:
                out.append(_ESCAPES[ch])
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch in "}]":
            # 回退掉紧邻的尾逗号（含其间空白）
            trailing: list[str] = []
            while out and out[-1].isspace():
                trailing.append(out.pop())
            if out and out[-1] == ",":
                out.pop()
            out.extend(reversed(trailing))
        out.append(ch)
    return "".join(out)


def parse_json_object(text: str) -> dict:
    """容忍 ```json 围栏，其余一律视为格式错误。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # 纯语法修复：字符串里的裸换行转义、去掉尾逗号。一个字的内容都不改。
        # 与「抽取器不许自动修复」不冲突——那条禁的是替模型补内容。
        try:
            data = json.loads(_repair_json(stripped))
        except json.JSONDecodeError as exc:
            raise DrafterOutputError(f"not valid JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise DrafterOutputError(f"top level is not an object: {text[:200]}")
    return data


def draft_fields(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    neighbors: list[str],
    model: str,
) -> dict[str, Field]:
    user = (
        f"Control: {control_id}\n"
        f"Framework text (public domain): {outcome}\n"
        f"Officially mapped 800-53 controls: {', '.join(neighbors) if neighbors else '(none)'}"
    )
    raw = client.complete(
        load_prompt("drafter"), [Message(role="user", content=user)], model=model
    )
    data = parse_json_object(raw)

    for name in DRAFTED_FIELDS:
        if name not in data or data[name] in (None, "", {}, []):
            raise DrafterOutputError(f"missing or empty field: {name}")

    # 逐字段验型。Field.value 是 str | list | dict | None，Pydantic 什么形状都收，
    # 所以 schema 拦不住——实测有厂商会把 evidence 也按三档返回。
    for name in ("intent", "plain_zh", "evidence"):
        if not isinstance(data[name], str):
            raise DrafterOutputError(
                f"{name} must be a string, got {type(data[name]).__name__}: {data[name]!r}"
            )

    practice = data["practice"]
    if not isinstance(practice, dict) or set(practice) != {"1", "2", "3"}:
        raise DrafterOutputError(f"practice must be a three-level dict, got: {practice!r}")
    bad_levels = {k: v for k, v in practice.items() if not isinstance(v, str)}
    if bad_levels:
        raise DrafterOutputError(f"every level of practice must be a string, got: {bad_levels!r}")

    # 差异化字段一律丢弃，即使模型主动给了。
    return {
        name: Field(value=data[name], basis=Basis.INFERRED) for name in DRAFTED_FIELDS
    }


def _check_str(data: dict, name: str, *, allow_null: bool) -> None:
    value = data.get(name)
    if value is None and allow_null:
        return
    if not isinstance(value, str) or not value.strip():
        raise DrafterOutputError(
            f"{name} must be a non-empty string{' or null' if allow_null else ''}, "
            f"got {type(value).__name__}: {value!r}"
        )


def _render_examples(examples: list) -> str:
    """把手写样例渲染成 few-shot。传递的是颗粒度，不是内容。"""
    if not examples:
        return ""
    blocks: list[str] = []
    for ex in examples:
        payload = {
            name: ex.fields[name].value for name in ALL_FIELDS if name in ex.fields
        }
        blocks.append(
            f"### Example: {ex.control_id}\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n```"
        )
    return (
        "\n\n---\n\n## Granularity examples (hand-written by auditors; shown in another\n"
    "## language - your output stays in English)\n\n"
        "Below is what the same set of fields looks like at **acceptable granularity**. **Do not copy their content** - "
        "they describe other controls. What to learn is the level of specificity:\n\n"
        "- `auditor_asks`: sentences an auditor would actually say out loud, the kind where a non-answer gives you away, "
        '- not yes/no questions such as "do you have a periodic process";\n'
        '- `common_myth`: names **exactly which step is wrong**, not correct-sounding platitudes such as "not enough emphasis";\n'
        "- `regional_note`: null when there is no real regional difference.\n\n"
        + "\n\n".join(blocks)
    )


def draft_full_fields(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    neighbors: list[str],
    model: str,
    label: str = "",
    grounding: list[str] | None = None,
    practice: list[str] | None = None,
    examples: list | None = None,
    failure_dir: Path | None = DRAFT_FAILURE_DIR,
) -> dict[str, Field]:
    """B 路线：七个字段全部由 AI 撰写。主 spec §5（2026-08-20 修订）

    产出一律标 basis=inferred——AI 写的就说是 AI 写的。作者事后在 $EDITOR 里
    改过的字段，由签字流程改标 practitioner。

    `outcome` 为空 = 这个框架的原文受版权保护，不给也不许给（主 spec §4.1、§9）。
    这时接地材料是自写标题 + `grounding`（官方映射到的 800-53 原文，公共领域）。
    必须把「原文没给」这件事写进 payload——否则模型会把自写标题当标准原文去翻译。

    `practice` 是**本组织自己制度里的节选**（用户上传的配套文档，设计 §8 S5）。
    它和 `grounding` 分开写进 payload，因为两者说的不是一回事：800-53 说的是
    「应该是什么」，自家制度说的是「现在是什么」。混在一起，模型会把
    「我们已经这么做了」写成「标准要求这么做」。
    """
    if outcome:
        user = (
            f"Control: {control_id}\n"
            f"Framework text (public domain): {outcome}\n"
            f"Officially mapped 800-53 controls: {', '.join(neighbors) if neighbors else '(none)'}"
        )
    else:
        lines = [
            f"Control: {control_id}",
            "The original text of this framework's control is **copyrighted and not provided**. Do not pretend to have read it, and do not translate "
            "the short title on the next line - that is our own short title, not the standard's text.",
            f"Self-written title: {label}",
        ]
        if grounding:
            lines.append(
                "Original text of the officially mapped NIST SP 800-53 controls (public domain; infer from it what this control defends against):"
            )
            lines += [f"- {line}" for line in grounding]
        else:
            lines.append("There is no official mapping to lean on; infer from the title and the framework structure.")
        user = "\n".join(lines)
    if practice:
        user += "\n" + _our_practice(practice)
    system = load_prompt("drafter_full") + _render_examples(examples or [])
    raw = client.complete(system, [Message(role="user", content=user)], model=model)

    def fail(message: str) -> DrafterOutputError:
        if failure_dir is not None:
            failure_dir.mkdir(parents=True, exist_ok=True)
            name = control_id.replace(":", "_").replace("/", "_")
            (failure_dir / f"{name}.txt").write_text(raw, encoding="utf-8")
        return DrafterOutputError(message)

    try:
        data = parse_json_object(raw)
    except DrafterOutputError as exc:
        raise fail(str(exc)) from exc

    missing = [n for n in ALL_FIELDS if n not in data]
    if missing:
        raise fail(f"missing fields: {missing}")

    for name in ("intent", "plain_zh"):
        _check_str(data, name, allow_null=False)
    _check_str(data, "evidence", allow_null=False)
    for name in ("common_myth", "regional_note"):
        _check_str(data, name, allow_null=True)

    practice = data["practice"]
    if not isinstance(practice, dict) or set(practice) != {"1", "2", "3"}:
        raise DrafterOutputError(f"practice must be a three-level dict, got: {practice!r}")
    bad = {k: v for k, v in practice.items() if not isinstance(v, str)}
    if bad:
        raise DrafterOutputError(f"every level of practice must be a string, got: {bad!r}")

    asks = data["auditor_asks"]
    if asks is not None:
        if not isinstance(asks, list) or not all(isinstance(a, str) for a in asks):
            raise DrafterOutputError(
                f"auditor_asks must be a list of strings or null, got: {asks!r}"
            )

    return {name: Field(value=data[name], basis=Basis.INFERRED) for name in ALL_FIELDS}


def rewrite_field(
    client: LLMClient,
    *,
    control_id: str,
    field: str,
    label: str,
    current,
    instruction: str,
    model: str,
    outcome: str = "",
) -> object:
    """按用户的一句要求重写一个字段。2026-08-23

    这是「用户帮 AI 一起解读」的第三件：用户看得出哪儿不对，但未必想自己动笔。
    他给方向（「再具体点，带上系统名」），模型执行。

    产出仍然是 AI 写的，落盘时标 `inferred`——**要求是他提的，字是模型写的**。
    把它记成 practitioner 等于替用户认领了他没写过的话。
    """
    if not instruction.strip():
        raise DrafterOutputError("the instruction must not be empty; with no instruction there is nothing to rewrite")

    lines = [f"Control: {control_id}", f"Field: {field} ({label})"]
    if outcome:
        lines.append(f"Body of this control (the user's own policy text): {outcome}")
    else:
        lines.append("No body text is available for this control; rewrite from the field's current content only. Do not invent new facts.")
    lines.append(
        "Current content:\n" + json.dumps(current, ensure_ascii=False, indent=2)
    )
    lines.append(f"The user's instruction: {instruction.strip()}")

    raw = client.complete(
        load_prompt("rewriter"),
        [Message(role="user", content="\n\n".join(lines))],
        model=model,
    )
    data = parse_json_object(raw)
    if "value" not in data:
        raise DrafterOutputError(f"output has no value key: {sorted(data)}")
    return _checked_value(field, data["value"])


def _our_practice(lines: list[str]) -> str:
    """本组织自己制度里的节选。**必须和标准原文分开标注。**

    不分开的话，模型会把「我们已经这么做了」写成「标准要求这么做」——
    那正好把这个产品的立身之本（哪句话是谁说的）搞坏。
    """
    return "\n".join([
        "Excerpts from this organization's own policies (below is what this company **already does**, not what the standard requires):",
        *(f"- {line}" for line in lines),
        "When writing practice and evidence, align with the existing practices above, "
        "and quote their specific numbers and department names where appropriate. "
        "**Never invent specific numbers, cadences, or department names not listed above.**",
    ])


def _checked_value(field: str, value):
    """形状不对就退回。形状塌了，practice 会从三档变成一句话。"""
    if field == "practice":
        if not isinstance(value, dict) or set(value) != {"1", "2", "3"}:
            raise DrafterOutputError(f"practice must be a three-level dict, got: {value!r}")
        bad = {k: v for k, v in value.items() if not isinstance(v, str)}
        if bad:
            raise DrafterOutputError(f"every level of practice must be a string, got: {bad!r}")
        return value
    if field == "auditor_asks":
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(a, str) for a in value)
        ):
            raise DrafterOutputError(f"auditor_asks must be a list of strings, got: {value!r}")
        return value
    if value is not None and not isinstance(value, str):
        raise DrafterOutputError(f"{field} must be a string, got: {value!r}")
    return value
