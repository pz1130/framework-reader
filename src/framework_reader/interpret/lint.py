"""抽取忠实度的近似检查。W2 spec §2.3

拦得住整段编造，拦不住换近义词。真正的防线是 $EDITOR 里逐条签字。
不做成构建断言——误报率会毁掉手感。
"""
import re

from framework_reader.interpret.model import DIFFERENTIATING_FIELDS, Field, RawAnswer

# 法规条号、百分比、年份——提示词禁止 AI 编造这些，但禁令拦不住模型，
# 而 106 条没人有精力逐条核。凡命中一律标出来待人工核实。
# 控制编号（A.5.22 / AC-2 / DE.CM-01）不在此列，那是本产品自己的标识。
_CITATION_RE = re.compile(
    r"第\s*\d+\s*条"          # 第32条
    r"|Art(?:icle)?\.?\s*\d+"  # Article 32 / Art. 32
    r"|§\s*\d+"                # §32
    r"|\d+(?:\.\d+)?\s*%"     # 95%
    r"|(?:19|20)\d{2}\s*年"     # 2023年
)


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def bigram_overlap(text: str, source: str) -> float:
    """text 的字符二元组有多大比例能在 source 里找到。空字段记 1.0（留空是信号）。"""
    grams = _bigrams(text)
    if not grams:
        return 1.0
    source_grams = _bigrams(source)
    return len(grams & source_grams) / len(grams)


def _field_text(field: Field) -> str:
    value = field.value
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    if isinstance(value, dict):
        return "".join(str(v) for v in value.values())
    return str(value)


def field_scores(fields: dict[str, Field], answers: list[RawAnswer]) -> dict[str, float]:
    source = "".join(a.text for a in answers)
    return {
        name: bigram_overlap(_field_text(fields[name]), source)
        for name in DIFFERENTIATING_FIELDS
        if name in fields
    }


def flag_low_fidelity(scores: dict[str, float], threshold: float) -> list[str]:
    return sorted(name for name, score in scores.items() if score < threshold)


def suggest_threshold(scores: list[float], margin: float = 0.05) -> float:
    """标定：取人工判定为忠实抽取的最低重合度，再下调一档。"""
    if not scores:
        return 0.0
    return max(0.0, min(scores) - margin)


def unplaced_answers(
    fields: dict[str, Field], answers: list[RawAnswer], threshold: float = 0.5
) -> list[int]:
    """哪几答一个字都没进字段。

    抽取器只输出三个差异化字段；自适应的第 3 问可能问出没有落点的内容，
    此时整条答案会被丢掉。落不进可以，**静默消失不行**——这里把它们报出来，
    在 $EDITOR 里显示给作者。判定与模型无关，纯按字符二元组重合度算。
    """
    placed = "".join(_field_text(fields[n]) for n in DIFFERENTIATING_FIELDS if n in fields)
    out: list[int] = []
    for answer in sorted(answers, key=lambda a: a.n):
        if not answer.text.strip():
            continue
        if bigram_overlap(answer.text, placed) < threshold:
            out.append(answer.n)
    return out


def citation_flags(fields: dict[str, Field]) -> dict[str, list[str]]:
    """标出疑似法规条号、百分比、年份，交人工核实。

    不做成构建断言：有些引用是对的，一刀切会逼人删掉正确内容。
    但也绝不能不报——B 路线下这些字是卖给合规团队的，编错一个条号是真风险。
    """
    out: dict[str, list[str]] = {}
    for name, field in sorted(fields.items()):
        hits = sorted(set(_CITATION_RE.findall(_field_text(field))))
        if hits:
            out[name] = hits
    return out
