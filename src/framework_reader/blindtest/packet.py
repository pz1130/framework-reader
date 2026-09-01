"""packet 与 answer_key。spec §4

packet 发给评委，answer_key 不发。泄露断言在产出之前跑——不是产出之后再警告。
"""
import itertools
import json
import random
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from framework_reader.blindtest.variants import leak_hits

LETTERS = ("A", "B", "C")
VARIANTS = ("product", "bare", "original")

# 逃生口。逼评委三选一的话，三份都是垃圾时产品照样能赢 70%——
# 那个 70% 量不出任何东西。spec §5、§6
NONE_PICK = "none"
NONE_VARIANT = "none"

# 六种排列。整批发出去，保证每个变体在每个字母位上的次数被夹住。
_LAYOUTS = tuple(itertools.permutations(VARIANTS))

_FENCE = re.compile(r"^\s*(?:```|~~~)")
_HEADING = re.compile(r"^(#{1,6})(\s+)(.*)$")

_INSTRUCTIONS = """# Blind test sheet

Each control below comes with three write-ups, labelled A, B and C.
**The order of the three differs from control to control.**

For each control, answer one question:

> **You have to prepare audit materials for it next week - which one helps you most?**

On the answer sheet, write the control number and the letter you pick (e.g. `1=B`).
**If all three miss the mark for a control, write `1=none`** - do not force a pick;
a forced vote measures nothing.
If you have something to add - which one helped, where the others fell short - please
jot down a line or two; that matters more than the choice itself.

---
"""


class PacketLeakError(Exception):
    """packet 里出现了能暴露变体来源的字样。不产出。"""


class PacketItem(BaseModel):
    control_id: str
    product: str
    bare: str
    original: str


class AnswerKey(BaseModel):
    seed: int
    order: list[str]
    mapping: dict[str, dict[str, str]]
    bare_model: str = ""
    bare_prompt_version: str = ""
    # 原文含泄露词、进不了抽样框的条款。事后要能回答「为什么这条不可能被抽中」。
    excluded: dict[str, list[str]] = {}
    # 三份材料各自的总字数。篇幅差是本轮最大的混淆变量，量出来才好写进限制。
    lengths: dict[str, int] = {}
    # 抽样框的指纹。seed 配上不同的抽样框会抽出另一批题，只记 seed 防不住重抽。
    frame_fingerprint: str = ""


def _balanced_layouts(n: int, rng: random.Random) -> list[tuple[str, ...]]:
    """平衡随机：每轮把六种排列整批打乱后发出去。

    逐条独立 shuffle 在 n=10 时会撞出「甲 有 8 条都是同一个变体」的牌面
    （seed=42 实测），位置效应就和变体混在一起了。整批发保证每个变体在每个
    字母位上的次数被夹在窄区间内，顺序对评委仍不可预测。
    """
    out: list[tuple[str, ...]] = []
    while len(out) < n:
        block = list(_LAYOUTS)
        rng.shuffle(block)
        out += block
    return out[:n]


def _demote_headings(text: str) -> str:
    """把变体正文里的标题一律压到字母标题（###）之下。

    裸问的回答带 ### 三级小标题，和 packet 用来分隔甲乙丙的 `### 甲` 同级——
    渲染出来正文小标题会跟另外两份材料平起平坐，既难读，也是一条来源线索。
    只动井号数量，一个字都不改。
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            found = _HEADING.match(line)
            if found:
                hashes, gap, title = found.groups()
                line = "#" * min(len(hashes) + 3, 6) + gap + title
        out.append(line)
    return "\n".join(out)


def load_cached_items(path: Path, control_ids: Sequence[str]) -> list[PacketItem] | None:
    """复用上一次 prepare 已经落盘的三份变体。抽到同一批条款时返回它们，否则 None。

    裸问那份每条都要花钱调模型，而且重调会拿到不同的文字——那等于换了一份题。
    只要抽样没变，就不该再调一次。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = [PacketItem(**entry) for entry in raw]
    except (OSError, ValueError):
        return None
    if [item.control_id for item in items] != list(control_ids):
        return None
    return items


def build_packet(
    items: list[PacketItem],
    seed: int,
    *,
    bare_model: str = "",
    bare_prompt_version: str = "",
    excluded: dict[str, list[str]] | None = None,
    frame_fingerprint: str = "",
) -> tuple[str, AnswerKey]:
    for item in items:
        for name in VARIANTS:
            hits = leak_hits(getattr(item, name))
            if hits:
                raise PacketLeakError(
                    f"{item.control_id} - one write-up contains leak words {hits} - packet not produced"
                )

    rng = random.Random(seed)
    layouts = _balanced_layouts(len(items), rng)
    order = [item.control_id for item in items]
    mapping: dict[str, dict[str, str]] = {}
    blocks: list[str] = []

    for index, (item, layout) in enumerate(zip(items, layouts), start=1):
        mapping[item.control_id] = dict(zip(LETTERS, layout))

        blocks.append(f"## {index}. {item.control_id}\n")
        for letter in LETTERS:
            body = _demote_headings(getattr(item, mapping[item.control_id][letter]))
            blocks.append(f"### {letter}\n\n{body}\n")

    key = AnswerKey(
        seed=seed, order=order, mapping=mapping,
        bare_model=bare_model, bare_prompt_version=bare_prompt_version,
        excluded=excluded or {},
        frame_fingerprint=frame_fingerprint,
        lengths={name: sum(len(getattr(i, name)) for i in items) for name in VARIANTS},
    )
    return _INSTRUCTIONS + "\n".join(blocks), key
