"""校对 pass：只改语言，不改内容。

起草出来的 106 条里约两三成带可见语病（句子断裂、语域错词、引号混用）。
内容层够用，坏在语言层——这一遍专治语言。

**风险与对策**：让模型重写文字，它顺手改掉意思是必然会发生的。因此每一处改动
都要过 `classify_edit`：只有判定为纯语言修正才自动落盘，其余一律拦下来交人看。
"""
import re

from pydantic import BaseModel

from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.lint import bigram_overlap
from framework_reader.interpret.model import ALL_FIELDS, Basis, Field
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

# 事实性记号：数字、年份、百分比、以及 GDPR / NIS2 / SIEM / DBA 这类拉丁串。
# 校对前后这些必须一个不多一个不少。
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.-]{1,}|\d+")

# 语言修正不该把句子改得面目全非。低于此重合度即视为改写而非校对。
_MIN_OVERLAP = 0.6

# 语域漂移：口语 → 书面语。分类器抓不到这类，因为内容没动——但 auditor_asks
# 的价值恰恰在于它像人说出口的话，书面化等于把卖点抹掉。实测校对模型会这么干。
_DRIFT_PAIRS = (
    ("在哪", "在何处"),
    ("问", "询问"),
    ("没人", "无人"),
    ("怎么", "如何"),
    ("多久", "多长时间"),
    ("有没有", "是否存在"),
    ("说", "阐述"),
    ("看", "查阅"),
)


def _register_drift(before: str, after: str) -> list[str]:
    """列出「口语被改成书面语」的位置。只在原文有、改文没有时才算。"""
    hits: list[str] = []
    for spoken, formal in _DRIFT_PAIRS:
        if formal not in after or formal in before:
            continue
        # 书面词常把口语词整个包住（询问 ⊃ 问），先把它抠掉再数，否则数不出减少。
        if after.replace(formal, "").count(spoken) < before.replace(formal, "").count(spoken):
            hits.append(f"{spoken}→{formal}")
    return hits


class EditVerdict(BaseModel):
    ok: bool
    reason: str = ""


class EditFlag(BaseModel):
    field: str
    before: str
    after: str
    reason: str


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}. {v}" for k, v in sorted(value.items()))
    return str(value)


def classify_edit(before: str, after: str) -> EditVerdict:
    """判断一处改动是纯语言修正，还是动了内容。"""
    if before == after:
        return EditVerdict(ok=True)
    if before.strip() and not after.strip():
        return EditVerdict(ok=False, reason="content deleted wholesale")

    before_tokens = sorted(_TOKEN_RE.findall(before))
    after_tokens = sorted(_TOKEN_RE.findall(after))
    if before_tokens != after_tokens:
        lost = sorted(set(before_tokens) - set(after_tokens))
        gained = sorted(set(after_tokens) - set(before_tokens))
        bits = []
        if lost:
            bits.append(f"Lost {lost}")
        if gained:
            bits.append(f"Gained {gained}")
        return EditVerdict(ok=False, reason="; ".join(bits) or "fact markers changed")

    overlap = bigram_overlap(after, before)
    if overlap < _MIN_OVERLAP:
        return EditVerdict(ok=False, reason=f"Overlap {overlap:.2f} too low; reads like a rewrite, not a proofread")

    drift = _register_drift(before, after)
    if drift:
        return EditVerdict(ok=False, reason=f"Register drift (colloquial made formal): {'; '.join(drift)}")
    return EditVerdict(ok=True)


def proofread_fields(
    client: LLMClient,
    *,
    control_id: str,
    fields: dict[str, Field],
    model: str,
) -> tuple[dict[str, Field], list[EditFlag]]:
    """返回（校对后的字段, 被拦下的可疑改动）。可疑改动不落盘。"""
    payload = {name: fields[name].value for name in ALL_FIELDS if name in fields}
    user = f"Control: {control_id}\n\n" + _as_json(payload)
    data = parse_json_object(
        client.complete(
            load_prompt("proofreader"), [Message(role="user", content=user)], model=model
        )
    )

    out: dict[str, Field] = dict(fields)
    flags: list[EditFlag] = []
    for name in ALL_FIELDS:
        if name not in fields or name not in data:
            continue
        original = fields[name]
        # 作者亲手写的，模型一个字都不许碰。
        if original.basis is Basis.PRACTITIONER:
            continue
        before, after = _text(original.value), _text(data[name])
        verdict = classify_edit(before, after)
        if not verdict.ok:
            flags.append(EditFlag(
                field=name, before=before, after=after, reason=verdict.reason
            ))
            continue
        out[name] = Field(value=data[name], basis=original.basis)
    return out, flags


def _as_json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
