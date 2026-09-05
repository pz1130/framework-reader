# W3：盲测工装 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成盲测工装——分层抽 10 条、生成三份去标识变体、产出可发给评委的 packet 与不可发的答案、回收判定并按写死的通过线出报告。

**Architecture:** 抽样、变体渲染、packet 组装、回收统计四个模块各自独立且纯函数化，模型调用（仅变体 b 需要）由调用方注入。通过线是模块级常量，不接受任何参数覆盖。packet 产出前做泄露断言，断言不过就不产出。

**Tech Stack:** Python 3.12、Pydantic v2、Typer、pytest（不引入新依赖）

**Spec:** `docs/superpowers/specs/2026-08-21-w3-blind-test-design.md`（上游：`docs/superpowers/specs/2026-08-19-framework-reader-design.md` §7.3、§7.4、§11 R4）

## Global Constraints

- **通过线是模块级常量**：`PASS_RATE = 0.70`、`MIN_ADVOCATES = 2`。**不接受命令行参数覆盖、不接受配置文件覆盖、不接受函数参数覆盖。** 要改必须改代码。（spec §6，主 spec §7.3「事前定死，事后不得修改」）
- **packet 不得泄露变体来源**：不得出现 `interpretation`、`basis`、`provenance`、`inferred`、`practitioner`、`draft`、`confirmed` 等字样，也不得出现变体名 (a)/(b)/(c)。断言不过就不产出 packet。（spec §4）
- **(b) 的提示词 pin 死在代码里**，与起草器同模型（`deepseek-chat`）。（spec §1）
- **seed 必须记进 answer_key 与 report**，防止事后重抽。（spec §3）
- **公有 CI 零网络、零 API key**，(b) 的生成走注入的 fake client。（spec §8）
- **结论措辞边界**：本轮只能声称「产品比裸问更有用」，不得声称「我们的文字比模型的文字好」；赢过 (c) 不算成绩。report 里必须打印这两句。（spec §1.1、§2）
- **不做**：网页表单收集、统计显著性检验、签字入库、第二轮统一结构加试。（spec §7）

## File Structure

| 文件 | 职责 |
|---|---|
| `src/framework_reader/blindtest/__init__.py` | 空 |
| `src/framework_reader/blindtest/sample.py` | 分层配额与可复现抽样 |
| `src/framework_reader/blindtest/variants.py` | 三份变体的渲染与生成、泄露词表 |
| `src/framework_reader/blindtest/packet.py` | packet.md 与 answer_key.json 组装、泄露断言 |
| `src/framework_reader/blindtest/tally.py` | 判定录入、报告计算、通过线常量 |
| `src/framework_reader/prompts/bare_llm.md` | 变体 (b) 的朴素提示词 |
| `src/framework_reader/cli/main.py` | 挂 `fr blindtest prepare/tally/report` |

---

### Task 1: 分层抽样

**Files:**
- Create: `src/framework_reader/blindtest/__init__.py`（空文件）
- Create: `src/framework_reader/blindtest/sample.py`
- Test: `tests/blindtest/test_sample.py`

**Interfaces:**
- Consumes: 无
- Produces: `function_of(control_id) -> str`、`quotas(counts: dict[str, int], total: int) -> dict[str, int]`、`stratified_sample(control_ids: list[str], n: int, seed: int) -> list[str]`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_sample.py`：

```python
import pytest

from framework_reader.blindtest.sample import function_of, quotas, stratified_sample

# 106 条的真实构成（2026-08-21 实测）
REAL_COUNTS = {"GV": 31, "PR": 22, "ID": 21, "RS": 13, "DE": 11, "RC": 8}


def _corpus() -> list[str]:
    out = []
    for func, count in REAL_COUNTS.items():
        out += [f"NIST-CSF-2.0:{func}.XX-{i:02d}" for i in range(1, count + 1)]
    return sorted(out)


def test_function_is_the_two_letters_before_the_dot():
    assert function_of("NIST-CSF-2.0:DE.CM-01") == "DE"
    assert function_of("NIST-CSF-2.0:GV.SC-07") == "GV"


def test_quotas_match_the_spec_table():
    """spec §3：GV 3 / PR 2 / ID 2 / RS 1 / DE 1 / RC 1，合计 10。"""
    assert quotas(REAL_COUNTS, 10) == {"GV": 3, "PR": 2, "ID": 2, "RS": 1, "DE": 1, "RC": 1}


def test_quotas_always_sum_to_n():
    assert sum(quotas(REAL_COUNTS, 10).values()) == 10
    assert sum(quotas(REAL_COUNTS, 7).values()) == 7
    assert sum(quotas({"A": 1, "B": 1, "C": 1}, 2).values()) == 2


def test_quotas_are_not_hardcoded_to_the_current_corpus():
    """106 条的构成会变（补入替代条），配额必须跟着变，不能写死。"""
    assert quotas({"GV": 50, "DE": 50}, 10) == {"GV": 5, "DE": 5}


def test_sample_is_reproducible_for_a_seed():
    corpus = _corpus()
    assert stratified_sample(corpus, 10, seed=42) == stratified_sample(corpus, 10, seed=42)


def test_different_seeds_give_different_samples():
    corpus = _corpus()
    assert stratified_sample(corpus, 10, seed=42) != stratified_sample(corpus, 10, seed=43)


def test_sample_respects_the_quota_per_function():
    from collections import Counter

    picked = stratified_sample(_corpus(), 10, seed=42)
    assert len(picked) == 10
    assert Counter(function_of(c) for c in picked) == {
        "GV": 3, "PR": 2, "ID": 2, "RS": 1, "DE": 1, "RC": 1
    }


def test_sample_has_no_duplicates():
    picked = stratified_sample(_corpus(), 10, seed=7)
    assert len(set(picked)) == 10


def test_asking_for_more_than_the_corpus_fails_loudly():
    with pytest.raises(ValueError, match="不足"):
        stratified_sample(["NIST-CSF-2.0:GV.A-01"], 10, seed=1)
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_sample.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.blindtest`

- [x] **Step 3: 写最小实现**

`src/framework_reader/blindtest/__init__.py`：空文件。

`src/framework_reader/blindtest/sample.py`：

```python
"""盲测抽样。spec §3

按 CSF 六个 function 分层：纯随机可能抽出一堆治理条款，分层保证覆盖面。
配额按最大余数法从实际构成算出，不写死——106 条的构成若变，配额自动跟着变。
"""
import random


def function_of(control_id: str) -> str:
    """`NIST-CSF-2.0:DE.CM-01` → `DE`"""
    local = control_id.split(":", 1)[-1]
    return local.split(".", 1)[0]


def quotas(counts: dict[str, int], total: int) -> dict[str, int]:
    """最大余数法：先取整数部分，余下的名额按小数部分从大到小分配。"""
    population = sum(counts.values())
    if population == 0:
        return {}
    exact = {name: count * total / population for name, count in counts.items()}
    out = {name: int(value) for name, value in exact.items()}
    remaining = total - sum(out.values())
    order = sorted(
        counts, key=lambda name: (exact[name] - int(exact[name]), counts[name], name),
        reverse=True,
    )
    for name in order[:remaining]:
        out[name] += 1
    return out


def stratified_sample(control_ids: list[str], n: int, seed: int) -> list[str]:
    if len(control_ids) < n:
        raise ValueError(f"语料不足：只有 {len(control_ids)} 条，要抽 {n} 条")

    buckets: dict[str, list[str]] = {}
    for control_id in sorted(control_ids):
        buckets.setdefault(function_of(control_id), []).append(control_id)

    counts = {name: len(items) for name, items in buckets.items()}
    plan = quotas(counts, n)

    rng = random.Random(seed)
    picked: list[str] = []
    for name in sorted(plan):
        take = min(plan[name], len(buckets[name]))
        picked += rng.sample(buckets[name], take)
    return sorted(picked)
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/blindtest/test_sample.py -v`
Expected: 9 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/blindtest/ tests/blindtest/
git commit -m "feat(blindtest): 按 CSF function 分层抽样，配额按最大余数法算"
```

---

### Task 2: 三份变体的渲染与泄露词表

**Files:**
- Create: `src/framework_reader/blindtest/variants.py`
- Test: `tests/blindtest/test_variants.py`

**Interfaces:**
- Consumes: W2 的 `Interpretation`
- Produces: `LEAK_WORDS: tuple[str, ...]`、`leak_hits(text) -> list[str]`、`render_product(interp) -> str`、`render_original(outcome) -> str`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_variants.py`：

```python
from framework_reader.blindtest.variants import (
    LEAK_WORDS,
    leak_hits,
    render_original,
    render_product,
)
from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
)


def _interp() -> Interpretation:
    fields = {n: Field(value=f"{n} 的内容", basis=Basis.INFERRED) for n in ALL_FIELDS}
    fields["practice"] = Field(
        value={"1": "一档", "2": "二档", "3": "三档"}, basis=Basis.INFERRED
    )
    fields["auditor_asks"] = Field(value=["追问一", "追问二"], basis=Basis.INFERRED)
    fields["regional_note"] = Field(value=None, basis=Basis.INFERRED)
    return Interpretation(control_id="NIST-CSF-2.0:DE.CM-01", fields=fields)


def test_product_render_shows_every_non_empty_field():
    text = render_product(_interp())
    assert "intent 的内容" in text
    assert "一档" in text and "三档" in text
    assert "追问一" in text and "追问二" in text


def test_empty_field_is_omitted_not_rendered_as_null():
    """留空的字段不该以「None」「null」的样子出现在评委眼前。"""
    text = render_product(_interp())
    assert "None" not in text and "null" not in text


def test_product_render_leaks_nothing():
    assert leak_hits(render_product(_interp())) == []


def test_leak_words_cover_the_spec_list():
    for word in ("interpretation", "basis", "provenance", "inferred", "practitioner"):
        assert word in LEAK_WORDS


def test_leak_detection_is_case_insensitive():
    assert leak_hits("这里有 Provenance 字样") == ["provenance"]


def test_leak_detection_finds_multiple():
    assert sorted(leak_hits("basis 与 inferred 都在")) == ["basis", "inferred"]


def test_control_id_is_not_a_leak():
    """三份变体共享同一控制编号，它不泄露来源，必须保留——评委要知道在评哪条。"""
    assert leak_hits("NIST-CSF-2.0:DE.CM-01") == []


def test_original_render_is_just_the_outcome_text():
    text = render_original("Networks and network services are monitored")
    assert "Networks and network services are monitored" in text
    assert leak_hits(text) == []
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_variants.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.blindtest.variants`

- [x] **Step 3: 写最小实现**

`src/framework_reader/blindtest/variants.py`：

```python
"""三份变体的渲染与泄露检测。spec §4

评委看到的每一个字都从这里出去。凡是能暴露「哪份是产品」的词，一律拦在这里。
"""
from framework_reader.interpret.model import Interpretation

# 出现即泄露来源。控制编号不在此列——三份变体共享它，评委也需要它。
LEAK_WORDS = (
    "interpretation",
    "basis",
    "provenance",
    "inferred",
    "practitioner",
    "framework_reader",
    "drafter",
    "prompt_version",
)

_FIELD_LABELS = (
    ("intent", "这条在防什么"),
    ("plain_zh", "大白话"),
    ("practice", "怎么落地"),
    ("evidence", "拿什么当证据"),
    ("common_myth", "常见误解"),
    ("auditor_asks", "会被追问什么"),
    ("regional_note", "地域差异"),
)


def leak_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in LEAK_WORDS if word in lowered]


def render_product(interp: Interpretation) -> str:
    lines: list[str] = []
    for name, label in _FIELD_LABELS:
        field = interp.fields.get(name)
        if field is None or field.value in (None, "", [], {}):
            continue          # 留空的字段直接不出现，不显示 None/null
        lines.append(f"**{label}**")
        value = field.value
        if isinstance(value, dict):
            for level, body in sorted(value.items()):
                lines.append(f"- {level} 档：{body}")
        elif isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines).strip()


def render_original(outcome: str) -> str:
    return outcome.strip()
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/blindtest/test_variants.py -v`
Expected: 8 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/blindtest/variants.py tests/blindtest/test_variants.py
git commit -m "feat(blindtest): 变体渲染与泄露词检测"
```

---

### Task 3: 变体 (b)——裸问同一个模型

**Files:**
- Create: `src/framework_reader/prompts/bare_llm.md`
- Modify: `src/framework_reader/prompts/__init__.py`
- Modify: `src/framework_reader/blindtest/variants.py`
- Test: `tests/blindtest/test_bare_llm.py`

**Interfaces:**
- Consumes: Task 2 的 `leak_hits`、W2 的 `LLMClient` / `Message`
- Produces: `render_bare(client, *, control_id, outcome, model) -> str`；`PROMPT_VERSIONS["bare_llm"] = "2026.08-b1"`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_bare_llm.py`：

```python
from framework_reader.blindtest.variants import leak_hits, render_bare
from framework_reader.llm.client import FakeClient


def _call(response: str = "这是模型的回答"):
    client = FakeClient([response])
    text = render_bare(
        client,
        control_id="NIST-CSF-2.0:DE.CM-01",
        outcome="Networks and network services are monitored",
        model="deepseek-chat",
    )
    return client, text


def test_returns_the_model_answer_verbatim():
    _, text = _call("监控要覆盖全部网段并有人处置告警")
    assert text == "监控要覆盖全部网段并有人处置告警"


def test_the_question_looks_like_a_peer_asking_a_chatbot():
    """spec §1：朴素提示词。给对照组精心调过的提示词是造一个不存在的对手。"""
    client, _ = _call()
    asked = client.calls[0]["messages"][0]["content"]
    assert "DE.CM-01" in asked
    assert "Networks and network services are monitored" in asked
    assert "审计" in asked


def test_bare_prompt_gives_no_product_structure():
    """不得把七字段结构送给对照组——那是产品的一部分。"""
    client, _ = _call()
    whole = client.calls[0]["system"] + client.calls[0]["messages"][0]["content"]
    for field in ("common_myth", "auditor_asks", "regional_note", "plain_zh"):
        assert field not in whole


def test_bare_output_is_checked_for_leaks():
    _, text = _call("回答里不该有 provenance 这种词")
    assert leak_hits(text) == ["provenance"], "泄露检测要能作用在模型输出上"


def test_prompt_version_is_pinned():
    from framework_reader.prompts import PROMPT_VERSIONS

    assert PROMPT_VERSIONS["bare_llm"] == "2026.08-b1"
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_bare_llm.py -v`
Expected: FAIL —— `ImportError: cannot import name 'render_bare'`

- [x] **Step 3: 写提示词**

`src/framework_reader/prompts/bare_llm.md`：

```markdown
你是一个通用助手。用简体中文回答用户的问题。
```

**这个提示词的朴素是有意的**（spec §1）：对照组模拟的是同行随手打开聊天框，
不是一个精心调过提示词的对手。**不要"改进"它。**

- [x] **Step 4: 写最小实现**

`src/framework_reader/prompts/__init__.py` 的 `PROMPT_VERSIONS` 中加入：

```python
    "bare_llm": "2026.08-b1",
```

`src/framework_reader/blindtest/variants.py` 追加：

```python
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

# 同行随手会问的那句话。spec §1：朴素，不调优。
_BARE_QUESTION = (
    "{control_id} 这条控制要求做什么？我下周要为它准备审计材料。\n"
    "框架原文是：{outcome}"
)


def render_bare(
    client: LLMClient, *, control_id: str, outcome: str, model: str
) -> str:
    """变体 (b)：与起草器同一个模型，朴素提示词，无框架接地、无结构要求。"""
    question = _BARE_QUESTION.format(control_id=control_id, outcome=outcome)
    return client.complete(
        load_prompt("bare_llm"),
        [Message(role="user", content=question)],
        model=model,
    ).strip()
```

- [x] **Step 5: 运行测试确认通过**

Run: `pytest tests/blindtest/test_bare_llm.py -v`
Expected: 5 passed

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/prompts/bare_llm.md src/framework_reader/prompts/__init__.py \
        src/framework_reader/blindtest/variants.py tests/blindtest/test_bare_llm.py
git commit -m "feat(blindtest): 变体 b——同模型裸问，提示词 pin 死"
```

---

### Task 4: Packet 与 answer_key

**Files:**
- Create: `src/framework_reader/blindtest/packet.py`
- Test: `tests/blindtest/test_packet.py`

**Interfaces:**
- Consumes: Task 1 `stratified_sample`、Task 2/3 的渲染函数
- Produces: `PacketLeakError`、`PacketItem`、`AnswerKey`、`LETTERS = ("甲", "乙", "丙")`、`VARIANTS = ("product", "bare", "original")`、`build_packet(items, seed, *, bare_model="", bare_prompt_version="") -> tuple[str, AnswerKey]`
  - `items: list[PacketItem]`，`PacketItem(control_id, product, bare, original)`
  - `AnswerKey(seed, order: list[str], mapping: dict[str, dict[str, str]], bare_model, bare_prompt_version)`
  - `mapping[control_id]["甲"] == "product" | "bare" | "original"`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_packet.py`：

```python
import pytest

from framework_reader.blindtest.packet import (
    PacketItem,
    PacketLeakError,
    build_packet,
)

LETTERS = ("甲", "乙", "丙")


def _items(n: int = 3) -> list[PacketItem]:
    return [
        PacketItem(
            control_id=f"NIST-CSF-2.0:GV.OC-0{i}",
            product=f"产品解读 {i}",
            bare=f"裸问回答 {i}",
            original=f"Original outcome {i}",
        )
        for i in range(1, n + 1)
    ]


def test_packet_contains_every_control_and_all_three_variants():
    text, _ = build_packet(_items(), seed=42)
    for i in (1, 2, 3):
        assert f"GV.OC-0{i}" in text
        assert f"产品解读 {i}" in text
        assert f"裸问回答 {i}" in text
        assert f"Original outcome {i}" in text


def test_packet_labels_variants_as_letters_only():
    text, _ = build_packet(_items(1), seed=42)
    for letter in LETTERS:
        assert letter in text
    assert "product" not in text and "bare" not in text and "original" not in text


def test_answer_key_maps_every_letter_for_every_control():
    _, key = build_packet(_items(), seed=42)
    for control_id in key.order:
        assert set(key.mapping[control_id]) == set(LETTERS)
        assert sorted(key.mapping[control_id].values()) == ["bare", "original", "product"]


def test_letter_order_differs_across_controls():
    """同一份 packet 里逐条独立随机——否则评委从第一条就能推出后面九条。"""
    _, key = build_packet(_items(10), seed=42)
    layouts = {tuple(key.mapping[c][l] for l in LETTERS) for c in key.order}
    assert len(layouts) > 1


def test_same_seed_gives_the_same_packet():
    assert build_packet(_items(), seed=42)[0] == build_packet(_items(), seed=42)[0]


def test_answer_key_records_the_seed():
    _, key = build_packet(_items(), seed=99)
    assert key.seed == 99


def test_leaking_content_refuses_to_produce_a_packet():
    """断言不过就不产出 packet——不是产出后再警告。spec §4"""
    items = _items(1)
    items[0].product = "这段里混进了 provenance 字样"
    with pytest.raises(PacketLeakError, match="provenance"):
        build_packet(items, seed=42)


def test_leak_check_covers_the_bare_variant_too():
    items = _items(1)
    items[0].bare = "模型自己吐出了 practitioner 这个词"
    with pytest.raises(PacketLeakError, match="practitioner"):
        build_packet(items, seed=42)


def test_packet_has_answer_instructions_for_judges():
    text, _ = build_packet(_items(1), seed=42)
    assert "哪一份对你最有用" in text
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_packet.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.blindtest.packet`

- [x] **Step 3: 写最小实现**

`src/framework_reader/blindtest/packet.py`：

```python
"""packet 与 answer_key。spec §4

packet 发给评委，answer_key 不发。泄露断言在产出之前跑——不是产出之后再警告。
"""
import random

from pydantic import BaseModel

from framework_reader.blindtest.variants import leak_hits

LETTERS = ("甲", "乙", "丙")
VARIANTS = ("product", "bare", "original")

_INSTRUCTIONS = """# 盲测问卷

下面每一条控制给出三份材料，标为甲、乙、丙。**三份的顺序每条都不一样。**

对每一条，请回答一个问题：

> **下周你要为这条准备审计材料，哪一份对你最有用？**

在答题纸上写下条号与你选的字母即可（例如 `1=乙`）。
如果某一条你有话想说——哪份哪里好、哪里没用——请随手写一句，这比选项本身更有价值。

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


def build_packet(
    items: list[PacketItem],
    seed: int,
    *,
    bare_model: str = "",
    bare_prompt_version: str = "",
) -> tuple[str, AnswerKey]:
    for item in items:
        for name in VARIANTS:
            hits = leak_hits(getattr(item, name))
            if hits:
                raise PacketLeakError(
                    f"{item.control_id} 的一份材料含泄露字样 {hits}——packet 不产出"
                )

    rng = random.Random(seed)
    order = [item.control_id for item in items]
    mapping: dict[str, dict[str, str]] = {}
    blocks: list[str] = []

    for index, item in enumerate(items, start=1):
        shuffled = list(VARIANTS)
        rng.shuffle(shuffled)
        mapping[item.control_id] = dict(zip(LETTERS, shuffled))

        blocks.append(f"## {index}. {item.control_id}\n")
        for letter in LETTERS:
            body = getattr(item, mapping[item.control_id][letter])
            blocks.append(f"### {letter}\n\n{body}\n")

    key = AnswerKey(
        seed=seed, order=order, mapping=mapping,
        bare_model=bare_model, bare_prompt_version=bare_prompt_version,
    )
    return _INSTRUCTIONS + "\n".join(blocks), key
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/blindtest/test_packet.py -v`
Expected: 9 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/blindtest/packet.py tests/blindtest/test_packet.py
git commit -m "feat(blindtest): packet 组装与泄露断言，逐条独立随机顺序"
```

---

### Task 5: 判定录入与报告（含写死的通过线）

**Files:**
- Create: `src/framework_reader/blindtest/tally.py`
- Test: `tests/blindtest/test_tally.py`

**Interfaces:**
- Consumes: Task 4 的 `AnswerKey`
- Produces: `PASS_RATE = 0.70`、`MIN_ADVOCATES = 2`、`ADVOCACY_MARKERS`、`Verdict`、`parse_picks(text) -> dict[int, str]`、`resolve(key, verdict) -> dict[str, str]`、`Report`、`build_report(key, verdicts) -> Report`、`render_report(report) -> str`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_tally.py`：

```python
import inspect

import pytest

from framework_reader.blindtest.packet import AnswerKey
from framework_reader.blindtest.tally import (
    MIN_ADVOCATES,
    PASS_RATE,
    Verdict,
    build_report,
    parse_picks,
    render_report,
    resolve,
)

KEY = AnswerKey(
    seed=42,
    order=["C1", "C2", "C3", "C4"],
    mapping={
        "C1": {"甲": "product", "乙": "bare", "丙": "original"},
        "C2": {"甲": "bare", "乙": "product", "丙": "original"},
        "C3": {"甲": "original", "乙": "bare", "丙": "product"},
        "C4": {"甲": "product", "乙": "original", "丙": "bare"},
    },
    bare_model="deepseek-chat",
    bare_prompt_version="2026.08-b1",
)


def _verdict(name: str, picks: str, note: str = "") -> Verdict:
    return Verdict(judge=name, picks=parse_picks(picks), note=note)


# ---------- 通过线不可覆盖 ----------

def test_pass_line_matches_the_spec():
    assert PASS_RATE == 0.70
    assert MIN_ADVOCATES == 2


def test_pass_line_cannot_be_passed_in_as_a_parameter():
    """spec §6：事前定死，事后不得修改。改必须改代码，从而留在 git 记录里。"""
    for func in (build_report, render_report):
        params = set(inspect.signature(func).parameters)
        assert not params & {"pass_rate", "threshold", "min_advocates"}, func.__name__


# ---------- 解析 ----------

def test_parse_picks_reads_index_to_letter():
    assert parse_picks("1=甲, 2=乙,3=丙") == {1: "甲", 2: "乙", 3: "丙"}


def test_parse_picks_rejects_an_unknown_letter():
    with pytest.raises(ValueError, match="丁"):
        parse_picks("1=丁")


def test_resolve_maps_letters_back_to_variants():
    assert resolve(KEY, _verdict("A", "1=甲,2=乙")) == {"C1": "product", "C2": "product"}


# ---------- 统计 ----------

def test_report_counts_product_share():
    verdicts = [
        _verdict("A", "1=甲,2=乙,3=丙,4=甲"),   # 全选 product
        _verdict("B", "1=甲,2=甲,3=丙,4=乙"),   # product, bare, product, original
    ]
    report = build_report(KEY, verdicts)
    assert report.total_picks == 8
    assert report.product_picks == 6
    assert report.product_share == pytest.approx(0.75)


def test_report_also_shows_the_original_variant_count():
    """spec §5 第 3 项：(c) 的得票要列出来（仅供参考），不能不报。"""
    report = build_report(KEY, [_verdict("A", "1=丙,2=丙,3=甲,4=乙")])
    assert report.original_picks == 4


def test_product_vs_bare_ignores_original():
    """有意义的比较只有 (a) vs (b)。赢过英文原文不算成绩。spec §1.1"""
    verdicts = [_verdict("A", "1=甲,2=甲,3=甲,4=乙")]  # product, bare, original, original
    report = build_report(KEY, verdicts)
    assert report.product_vs_bare == pytest.approx(0.5)


def test_pass_needs_both_conditions():
    strong = [_verdict("A", "1=甲,2=乙,3=丙,4=甲", "auditor_asks 那几句很有用"),
              _verdict("B", "1=甲,2=乙,3=丙,4=甲", "映射能看到出处，这个有价值")]
    assert build_report(KEY, strong).passed is True


def test_high_share_but_too_few_advocates_fails():
    quiet = [_verdict("A", "1=甲,2=乙,3=丙,4=甲", "还行"),
             _verdict("B", "1=甲,2=乙,3=丙,4=甲", "都差不多")]
    report = build_report(KEY, quiet)
    assert report.product_share == 1.0
    assert report.advocates == 0
    assert report.passed is False


def test_enough_advocates_but_low_share_fails():
    weak = [_verdict("A", "1=乙,2=甲,3=甲,4=乙", "common_myth 有价值"),
            _verdict("B", "1=乙,2=甲,3=甲,4=乙", "出处标得清楚")]
    report = build_report(KEY, weak)
    assert report.advocates == 2
    assert report.passed is False


def test_report_prints_the_wording_limits():
    """结论措辞边界必须印在报告上，不能只写在 spec 里。spec §1.1、§2"""
    text = render_report(build_report(KEY, [_verdict("A", "1=甲,2=乙,3=丙,4=甲")]))
    assert "不能声称" in text
    assert "赢过" in text


def test_report_records_seed_and_bare_model():
    text = render_report(build_report(KEY, [_verdict("A", "1=甲,2=乙,3=丙,4=甲")]))
    assert "42" in text and "deepseek-chat" in text and "2026.08-b1" in text
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_tally.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.blindtest.tally`

- [x] **Step 3: 写最小实现**

`src/framework_reader/blindtest/tally.py`：

```python
"""判定录入、统计与通过线。spec §5、§6

通过线是模块级常量，任何函数都不接受覆盖它的参数——主 spec §7.3 要求
「事前定死，事后不得修改」。要改必须改这两行，从而留在 git 记录里。
"""
from pydantic import BaseModel

from framework_reader.blindtest.packet import LETTERS, AnswerKey

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
    product_share: float
    product_vs_bare: float
    advocates: int
    passed: bool
    bare_model: str
    bare_prompt_version: str


def parse_picks(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for chunk in text.replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        index, _, letter = chunk.partition("=")
        letter = letter.strip()
        if letter not in LETTERS:
            raise ValueError(f"看不懂的选项：{letter}（只接受 {'/'.join(LETTERS)}）")
        out[int(index.strip())] = letter
    return out


def resolve(key: AnswerKey, verdict: Verdict) -> dict[str, str]:
    out: dict[str, str] = {}
    for index, letter in sorted(verdict.picks.items()):
        control_id = key.order[index - 1]
        out[control_id] = key.mapping[control_id][letter]
    return out


def build_report(key: AnswerKey, verdicts: list[Verdict]) -> Report:
    chosen: list[str] = []
    for verdict in verdicts:
        chosen += list(resolve(key, verdict).values())

    total = len(chosen)
    product = chosen.count("product")
    bare = chosen.count("bare")
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
        product_share=share,
        product_vs_bare=head_to_head,
        advocates=advocates,
        passed=share >= PASS_RATE and advocates >= MIN_ADVOCATES,
        bare_model=key.bare_model,
        bare_prompt_version=key.bare_prompt_version,
    )


def render_report(report: Report) -> str:
    verdict = "通过" if report.passed else "未通过"
    return "\n".join([
        f"盲测报告  seed={report.seed}  评委 {report.judges} 人",
        f"对照组 (b)：{report.bare_model} / 提示词 {report.bare_prompt_version}",
        "",
        f"本产品被选  {report.product_picks}/{report.total_picks}"
        f"  = {report.product_share:.0%}   （通过线 {PASS_RATE:.0%}）",
        f"仅与裸问对比  {report.product_vs_bare:.0%}   ← 有意义的那个数",
        f"三份得票  产品 {report.product_picks} / 裸问 {report.bare_picks}"
        f" / 框架原文 {report.original_picks}",
        f"主动指出价值的评委  {report.advocates} 人   （通过线 {MIN_ADVOCATES} 人）",
        "",
        f"判定：{verdict}",
        "",
        "措辞边界（spec §1.1、§2）：",
        "  本轮只能声称「这个产品比裸问更有用」，",
        "  不能声称「我们的文字比大模型的文字好」——结构差异没有剥离，那个没测过。",
        "  赢过变体 (c) 框架原文不算成绩：它是英文，中文读者读它天然吃亏。",
    ])
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/blindtest/test_tally.py -v`
Expected: 13 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/blindtest/tally.py tests/blindtest/test_tally.py
git commit -m "feat(blindtest): 判定统计与写死的通过线"
```

---

### Task 6: CLI 与收尾

**Files:**
- Modify: `src/framework_reader/cli/main.py`
- Modify: `README.md`
- Test: `tests/blindtest/test_cli.py`

**Interfaces:**
- Consumes: Task 1–5 全部
- Produces: `fr blindtest prepare --seed N`、`fr blindtest tally --seed N --judge X --picks "..."`、`fr blindtest report --seed N`

- [x] **Step 1: 写失败测试**

`tests/blindtest/test_cli.py`：

```python
from typer.testing import CliRunner

from framework_reader.cli.main import app


def test_blindtest_help_lists_three_actions():
    result = CliRunner().invoke(app, ["blindtest", "--help"])
    assert result.exit_code == 0
    for word in ("prepare", "tally", "report"):
        assert word in result.stdout


def test_unknown_action_exits_nonzero():
    result = CliRunner().invoke(app, ["blindtest", "nope"])
    assert result.exit_code != 0
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/blindtest/test_cli.py -v`
Expected: FAIL —— `No such command 'blindtest'`

- [x] **Step 3: 挂 CLI**

`src/framework_reader/cli/main.py` 追加：

```python
BLINDTEST_DIR = Path("build/blindtest")


@app.command("blindtest")
def blindtest(
    action: str = typer.Argument(..., help="prepare | tally | report"),
    seed: int = 42,
    judge: str = "",
    picks: str = "",
    note: str = "",
    n: int = 10,
    db: Path = DEFAULT_DB,
) -> None:
    """盲测：prepare 出题、tally 录判定、report 出结论。"""
    import json

    from framework_reader.blindtest.packet import AnswerKey, PacketItem, build_packet
    from framework_reader.blindtest.sample import stratified_sample
    from framework_reader.blindtest.tally import (
        Verdict, build_report, parse_picks, render_report,
    )
    from framework_reader.blindtest.variants import (
        render_bare, render_original, render_product,
    )
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.llm.guard import PayloadGuard
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
    from framework_reader.prompts import PROMPT_VERSIONS

    room = BLINDTEST_DIR / str(seed)
    key_path = room / "answer_key.json"

    if action == "prepare":
        store = InterpretationStore()
        api = QueryAPI(db)
        picked = stratified_sample([i.control_id for i in store.iter_all()], n, seed)
        registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
        client = registry.build("drafter", guard=PayloadGuard([]))
        model = registry.role("drafter").model

        items = []
        for control_id in picked:
            control = api.get_control(control_id)
            outcome = control.label if control else ""
            items.append(PacketItem(
                control_id=control_id,
                product=render_product(store.load(control_id)),
                bare=render_bare(
                    client, control_id=control_id, outcome=outcome, model=model
                ),
                original=render_original(outcome),
            ))

        text, key = build_packet(
            items, seed,
            bare_model=model, bare_prompt_version=PROMPT_VERSIONS["bare_llm"],
        )
        room.mkdir(parents=True, exist_ok=True)
        (room / "packet.md").write_text(text, encoding="utf-8")
        key_path.write_text(
            key.model_dump_json(indent=2), encoding="utf-8"
        )
        typer.echo(f"出题 {len(items)} 条 → {room / 'packet.md'}（这份发给评委）")
        typer.echo(f"答案 → {key_path}（这份别发）")
        raise typer.Exit(0)

    if not key_path.exists():
        typer.echo(f"没有 seed={seed} 的题目，先跑 fr blindtest prepare --seed {seed}")
        raise typer.Exit(1)
    key = AnswerKey(**json.loads(key_path.read_text(encoding="utf-8")))

    if action == "tally":
        if not judge or not picks:
            typer.echo("需要 --judge 与 --picks，例如 --picks \"1=甲,2=丙\"")
            raise typer.Exit(2)
        verdict = Verdict(judge=judge, picks=parse_picks(picks), note=note)
        out = room / "verdicts"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{judge}.json").write_text(
            verdict.model_dump_json(indent=2), encoding="utf-8"
        )
        typer.echo(f"已记录 {judge} 的 {len(verdict.picks)} 条判定")
        raise typer.Exit(0)

    if action == "report":
        files = sorted((room / "verdicts").glob("*.json")) if (room / "verdicts").exists() else []
        if not files:
            typer.echo("还没有任何评委的判定")
            raise typer.Exit(1)
        verdicts = [
            Verdict(**json.loads(p.read_text(encoding="utf-8"))) for p in files
        ]
        typer.echo(render_report(build_report(key, verdicts)))
        raise typer.Exit(0)

    typer.echo(f"未知操作：{action}")
    raise typer.Exit(2)
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/blindtest/test_cli.py -v && pytest -q`
Expected: 全绿

- [x] **Step 5: 更新 README**

`README.md` 的「解读生产（W2）」一节之后追加：

```markdown
## 盲测（W3）

```bash
fr blindtest prepare --seed 42        # 分层抽 10 条，出题；packet.md 发评委，answer_key.json 别发
fr blindtest tally --seed 42 --judge 老王 --picks "1=甲,2=丙,…" --note "追问那几句有用"
fr blindtest report --seed 42         # 出结论，含通过线判定与措辞边界
```

通过线（≥70%、至少 2 人主动指出价值）写死在 `blindtest/tally.py`，
不接受任何参数覆盖——要改必须改代码。
```

- [x] **Step 6: 在无 key 环境验证 CI 假设**

Run:
```bash
env -u DEEPSEEK_API_KEY -u MINIMAX_API_KEY pytest -q
```
Expected: 全绿（`prepare` 需要 key，但它不在测试路径上）

- [x] **Step 7: 提交**

```bash
git add src/framework_reader/cli/main.py README.md tests/blindtest/test_cli.py
git commit -m "feat(cli): fr blindtest prepare/tally/report"
```

- [x] **Step 8: W3 工装验收**

```bash
pytest -v                                  # 全绿
fr blindtest prepare --seed 42             # 出题
grep -ci "provenance\|inferred\|practitioner" build/blindtest/42/packet.md   # 应为 0
head -30 build/blindtest/42/packet.md      # 人眼确认读起来像一份问卷
```

验收标准：

- [x] `packet.md` 里 10 条、每条三份、只有甲乙丙，无任何变体标识
- [x] `answer_key.json` 记了 seed、逐条映射、(b) 的模型与提示词版本
- [x] 通过线常量无法被参数覆盖（有测试钉死）
- [x] 全部测试绿，且在无 API key 环境下绿

---

## W3 之后

**工装做完不等于这期做完。** 这期的产出是**盲测结论**，而它卡在人上：

- **3–5 位真正做过审计的同行**（主 spec §7.3）。没有他们，工装只是一个没跑过的脚本。
- 评委不得由作者本人或 AI 充当（R7 那次是 AI 自评，结论采信是因为 17% 离 30% 线足够远；盲测验的正是「人觉得哪份更有用」，AI 判定没有意义）。

盲测出结论之后：

- **通过** → 签字入库（本文档未覆盖，届时另行设计），然后进 W4 的 ISO；
- **未通过** → 按主 spec §7.3，先修解读的提示词与结构，跑第二轮；两轮仍不过，
  要么回 A 路线（作者亲自写差异化字段，访谈管线仍在、未删），要么停止。

另仍欠着（W2 遗留）：

- **OLIR #186 文件名含 `_draft`**，贡献 1182 条可导出边中的 743 条。W6 打包发布前必须回 OLIR 目录页重新核实。
- **`QueryAPI.search()` 只匹配 `label`**，现在有 106 条中文解读入库了，检索该重做。
