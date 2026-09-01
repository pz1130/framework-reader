"""黄金样例：管线的验收标准。W2 spec §1.3

零 AI 参与、手写、先于管线代码完成。管线只读不写本目录。
"""
from pathlib import Path

from framework_reader.interpret.model import Interpretation
from framework_reader.interpret.store import InterpretationStore

GOLDEN_ROOT = Path("content/golden")

# 按「差异化字段有没有料」挑，不按重要性挑。W2 spec §1.3
GOLDEN_CONTROLS = (
    "NIST-CSF-2.0:GV.SC-07",   # regional_note 最有料
    "NIST-CSF-2.0:PR.AA-05",   # common_myth 最有料
    "NIST-CSF-2.0:GV.RM-02",   # 故意挑难的务虚条款
)


def golden_store(root: Path = GOLDEN_ROOT) -> InterpretationStore:
    return InterpretationStore(root)


def load_golden(control_id: str, root: Path = GOLDEN_ROOT) -> Interpretation:
    return golden_store(root).load(control_id)


def few_shot_examples(
    exclude: str | None = None, root: Path = GOLDEN_ROOT
) -> list[Interpretation]:
    """手写黄金样例，用作起草提示词的 few-shot。

    目的是让模型学**颗粒度**（「抽三个离职账号，权限哪天收回的」而不是
    「是否有定期流程」），不是让它抄内容。

    `exclude` 必须传目标控制自己——拿 PR.AA-05 的手写版去生成 PR.AA-05
    不是学习，是抄答案，比出来的质量也没有意义。
    """
    store = golden_store(root)
    out: list[Interpretation] = []
    for control_id in GOLDEN_CONTROLS:
        if control_id == exclude or not store.exists(control_id):
            continue
        out.append(store.load(control_id))
    return out
