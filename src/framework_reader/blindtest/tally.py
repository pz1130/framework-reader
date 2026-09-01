"""判定录入、统计与通过线。spec §5、§6

通过线是模块级常量，任何函数都不接受覆盖它的参数——主 spec §7.3 要求
「事前定死，事后不得修改」。要改必须改这两行，从而留在 git 记录里。
"""
import re

from pydantic import BaseModel

from framework_reader.blindtest.packet import (
    LETTERS,
    NONE_PICK,
    NONE_VARIANT,
    AnswerKey,
)

# ↓↓↓ 通过线。改动必须单独提交并说明理由。spec §6 ↓↓↓
PASS_RATE = 0.70
MIN_ADVOCATES = 2
# ↑↑↑ 不要给它们加参数、加配置、加环境变量 ↑↑↑

# 评语里出现这些，算「主动指出价值」。主 spec §7.3 第二条通过线
ADVOCACY_MARKERS = (
    "common_myth", "误解",
    "auditor_asks", "追问",
    "regional_note", "地域",
    "映射", "出处",
)

_UNSAFE_IN_FILENAME = re.compile(r"[^\w-]")


class Verdict(BaseModel):
    judge: str
    picks: dict[int, str]
    note: str = ""


class Report(BaseModel):
    seed: int
    judges: int
    total_picks: int
    product_picks: int
    bare_picks: int
    original_picks: int
    # 「三份都没用」。算进分母、不算产品票——弃权不能替产品抬分。
    none_picks: int = 0
    product_share: float
    product_vs_bare: float
    advocates: int
    passed: bool
    bare_model: str
    bare_prompt_version: str
    # 每人交了几条。漏答会让分母静默变小，这件事必须写在脸上。
    picks_by_judge: dict[str, int] = {}
    expected_picks: int = 0
    # 评语原文。advocates 是子串匹配，分不清褒贬，得留证据给人核。
    notes: dict[str, str] = {}
    lengths: dict[str, int] = {}


def safe_judge_filename(judge: str) -> str:
    """评委名直接当文件名会写到目录外。只留下当文件名安全的字符。"""
    cleaned = _UNSAFE_IN_FILENAME.sub("_", judge).strip("_.")
    return cleaned or "judge"


def parse_picks(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for chunk in text.replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        index, _, letter = chunk.partition("=")
        letter = letter.strip()
        if letter not in LETTERS and letter != NONE_PICK:
            raise ValueError(
                f"Unrecognized pick: {letter} (only {'/'.join(LETTERS)}/{NONE_PICK})"
            )
        out[int(index.strip())] = letter
    return out


def resolve(key: AnswerKey, verdict: Verdict) -> dict[str, str]:
    out: dict[str, str] = {}
    n = len(key.order)
    for index, letter in sorted(verdict.picks.items()):
        if index < 1 or index > n:
            raise ValueError(f"Control number {index} out of range (1–{n})")
        control_id = key.order[index - 1]
        out[control_id] = (
            NONE_VARIANT if letter == NONE_PICK else key.mapping[control_id][letter]
        )
    return out


def build_report(key: AnswerKey, verdicts: list[Verdict]) -> Report:
    chosen: list[str] = []
    for verdict in verdicts:
        chosen += list(resolve(key, verdict).values())

    total = len(chosen)
    product = chosen.count("product")
    bare = chosen.count("bare")
    none = chosen.count(NONE_VARIANT)
    share = product / total if total else 0.0
    # 只在 product 与 bare 之间比。原文是英文，中文读者读它天然吃亏。
    head_to_head = product / (product + bare) if (product + bare) else 0.0

    advocates = sum(
        1 for v in verdicts if any(m in v.note for m in ADVOCACY_MARKERS)
    )
    return Report(
        seed=key.seed,
        judges=len(verdicts),
        total_picks=total,
        product_picks=product,
        bare_picks=bare,
        original_picks=chosen.count("original"),
        none_picks=none,
        product_share=share,
        product_vs_bare=head_to_head,
        advocates=advocates,
        passed=share >= PASS_RATE and advocates >= MIN_ADVOCATES,
        bare_model=key.bare_model,
        bare_prompt_version=key.bare_prompt_version,
        picks_by_judge={v.judge: len(v.picks) for v in verdicts},
        expected_picks=len(key.order),
        notes={v.judge: v.note for v in verdicts},
        lengths=key.lengths,
    )


def render_report(report: Report) -> str:
    verdict = "Pass" if report.passed else "Fail"
    lines = [
        f"Blind test report  seed={report.seed}  judges: {report.judges}",
        f"Control group (b): {report.bare_model} / prompt {report.bare_prompt_version}",
        "",
        f"Product picked  {report.product_picks}/{report.total_picks}"
        f"  = {report.product_share:.0%}   (pass line {PASS_RATE:.0%})",
        f"Head-to-head vs the bare ask  {report.product_vs_bare:.0%}   <- the meaningful number",
        f"Votes  product {report.product_picks} / bare {report.bare_picks}"
        f" / framework text {report.original_picks}"
        f" / all three {NONE_PICK} {report.none_picks}",
        f"Judges who called out specific value  {report.advocates}   (pass line {MIN_ADVOCATES})",
        "",
        f"Verdict: {verdict}",
        "",
        "Picks submitted per judge:",
    ]
    for judge, count in sorted(report.picks_by_judge.items()):
        flag = "   <- missing picks; the denominator counts submitted picks only" if count < report.expected_picks else ""
        lines.append(f"  {judge}  {count}/{report.expected_picks}{flag}")

    if report.none_picks:
        lines += [
            "",
            f"A total of {report.none_picks} votes picked all three {NONE_PICK}."
            "These votes count in the denominator but toward no write-up,",
            "  which pulls the product share down; the head-to-head (product vs bare) excludes them.",
        ]

    lines += [
        "",
        f"Verbatim notes (advocates={report.advocates} comes from keyword matching;",
        "  it cannot tell praise from dismissal - confirm by hand before trusting):",
    ]
    for judge in sorted(report.notes):
        lines.append(f"  {judge}: {report.notes[judge] or '(no note left)'}")

    if report.lengths.get("product"):
        ratio = report.lengths["bare"] / report.lengths["product"]
        lines += [
            "",
            "Known confound - length (spec section 2 assumed the control group was a prose paragraph; in practice it is not):",
            f"  product {report.lengths['product']} chars"
            f" / bare {report.lengths['bare']} chars"
            f" / framework text {report.lengths['original']} chars",
            f"  bare is {ratio:.1f}x the length of the product; how much of the choice was really about length is not separated this round.",
        ]

    lines += [
        "",
        "Wording limits (spec sections 1.1 and 2):",
        "  This round can only claim that the product beats the bare ask,",
        "  it cannot claim that our writing is better than the model's writing - the structural difference was not separated, and that was never measured.",
        "  Beating variant (c), the framework text, is not a win: it was not written to serve this task.",
    ]
    return "\n".join(lines)
