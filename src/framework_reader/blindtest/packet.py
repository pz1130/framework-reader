"""packet and answer_key. spec §4

The packet goes to the judges; the answer_key does not. The leak assertion runs before
anything is produced — not a warning after the fact.
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

# The escape hatch. If judges are forced into a three-way pick, the product can still win
# 70% when all three write-ups are garbage — and that 70% measures nothing. spec §5, §6
NONE_PICK = "none"
NONE_VARIANT = "none"

# The six permutations. Sent out as whole blocks, so the count of each variant in each
# letter position stays pinned.
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
    """The packet contains wording that could expose a variant's origin. Nothing is produced."""


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
    # Controls whose original text contains leak words and therefore never entered the
    # sampling frame. Afterwards we must be able to answer "why could this one never have
    # been sampled".
    excluded: dict[str, list[str]] = {}
    # Total word count of each of the three write-ups. Length difference is the biggest
    # confounder of this round; measuring it is what makes it writable into the limitations.
    lengths: dict[str, int] = {}
    # Fingerprint of the sampling frame. The same seed over a different frame samples a
    # different batch of questions; recording the seed alone does not prevent re-sampling.
    frame_fingerprint: str = ""


def _balanced_layouts(n: int, rng: random.Random) -> list[tuple[str, ...]]:
    """Balanced randomization: each round, all six permutations are shuffled as one
    batch and sent out.

    Independent per-item shuffling at n=10 can deal a hand where "A lands the same
    variant on 8 items" (observed with seed=42), and then position effects get confounded
    with the variants. Sending whole blocks guarantees the count of each variant in each
    letter position stays within a narrow band, while the order stays unpredictable to
    the judges.
    """
    out: list[tuple[str, ...]] = []
    while len(out) < n:
        block = list(_LAYOUTS)
        rng.shuffle(block)
        out += block
    return out[:n]


def _demote_headings(text: str) -> str:
    """Pushes every heading in a variant's body below the letter heading (###).

    The bare write-up's answers carry ### level-3 subheadings — the same level as the
    `### A` headings the packet uses to separate A/B/C. Rendered, the body subheadings
    would sit on equal footing with the other two write-ups, which is both hard to read
    and a source clue. Only the number of hashes changes; not a single word is touched.
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
    """Reuses the three variants the previous prepare already wrote to disk. Returns them
    when the same batch of controls was sampled; otherwise None.

    Every item of the bare write-up costs a paid model call, and calling again returns
    different wording — which amounts to a different question. As long as the sampling
    frame has not changed, it should not be called again.
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
