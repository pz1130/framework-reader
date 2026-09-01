# W2：访谈管线与解读生产 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成解读生产管线——AI 起草四个非差异化字段、AI 提问三轮、作者回答、严格抽取产出三个差异化字段、`$EDITOR` 签字入库——并用 3 条手写黄金样例验收，产出「一条控制端到端耗时」这个决定 W3 范围的数字。

**Architecture:** Pydantic 模型是 schema 单一真相源，解读以每控制一个 YAML 文件存于 Git（`content/interpretations/`），SQLite 仍是构建产物。模型调用走 `LLMClient` 协议，两个适配器（Anthropic 原生、OpenAI 兼容）覆盖全部预设厂商，四个调用点各自指定厂商。三个差异化字段禁止 AI 起草，只能由抽取器从作者原话中删/切/重排得到。

**Tech Stack:** Python 3.12、Pydantic v2、PyYAML、Typer、prompt_toolkit、anthropic SDK、httpx（OpenAI 兼容口）、pytest

**Spec:** `docs/superpowers/specs/2026-08-20-w2-interview-pipeline-design.md`（上游：`docs/superpowers/specs/2026-08-19-framework-reader-design.md`）

## Global Constraints

以下为 spec 的全局约束，每个 task 的要求都隐含包含本节。

- **三个差异化字段（`common_myth` / `auditor_asks` / `regional_note`）禁止 AI 起草。** 只能由抽取器从作者原话中删、切、重排得到，`basis` 恒为 `practitioner`。（W2 spec §1 D1、§2.3）
- **抽取器不得引入作者没说过的信息。** 不润色措辞、不补空缺。（W2 spec §2.3）
- **留空是信号，不是缺陷。** 差异化字段允许为 `null`，构建不得因此失败。（W2 spec §2.2）
- **AI 不能签字。** `confirmed_by` 必须是人；只有 `state == confirmed` 且 `confirmed_by` 非空的解读进构建产物。（W2 spec §4.3，主 spec §5）
- **Tier C/D 原文不得进入任何模型调用的 payload。** 在客户端出口处断言，抛异常，不重试、不降级、不降为 warn。（W2 spec §3.4③）
- **原文零内置**：`original_text` 表在构建产物中必须为空。（主 spec §3.2②、§4.2⑤）
- **公有 CI 不接触 `vendor/`、不接触 API key、零网络。** 全部模型调用走注入的 fake client。（W2 spec §7，主 spec §10.C）
- **`model_id` 记全**：每条解读记录 `provider` / `model` / `prompt_version`。（W2 spec §3.4②）
- **prompt caching 是 best-effort**，适配器接口不得含「必须命中缓存」的假设。（W2 spec §3.4①）
- **`locale` 字段从第一天存在**，当前只有 `zh-CN`。（主 spec §8⑤）
- **内容以 YAML 存于 Git，SQLite 是构建产物**，随时可重建，不进 Git。（主 spec §9）
- **`content/golden/` 管线只读不写。**（W2 spec §4.1）

**W2 不包含**：CSF 106 条实际生产（W3）、ISO 与其他框架（W4–W6）、映射 L4 初稿（W4–W5）、`QueryAPI.search()` 重做（W4）、Web UI 与用户可写层（B/C 阶段）。

## File Structure

新增：

| 文件 | 职责 |
|---|---|
| `src/framework_reader/interpret/model.py` | 解读的 Pydantic schema、状态机、字段不变式 |
| `src/framework_reader/interpret/store.py` | YAML 读写、原子 append、目录遍历 |
| `src/framework_reader/interpret/drafter.py` | 起草器：四个非差异化字段 |
| `src/framework_reader/interpret/questioner.py` | 提问器：Q1/Q2 固定模板、Q3 自适应 |
| `src/framework_reader/interpret/extractor.py` | 抽取器：严格抽取三个差异化字段 |
| `src/framework_reader/interpret/lint.py` | 抽取忠实度 lint（字符二元组重合度） |
| `src/framework_reader/interpret/compare.py` | `golden diff` 与跨厂商对比 |
| `src/framework_reader/llm/client.py` | `LLMClient` 协议、`ModelRef`、`FakeClient` |
| `src/framework_reader/llm/guard.py` | 出口红线：Tier C/D 原文不得出圈 |
| `src/framework_reader/llm/openai_compat.py` | OpenAI 兼容适配器 |
| `src/framework_reader/llm/anthropic_adapter.py` | Anthropic 原生适配器 |
| `src/framework_reader/llm/retry.py` | 退避重试（包在 guard 里面；红线异常不重试） |
| `src/framework_reader/llm/registry.py` | 预设加载、按角色组装 client |
| `src/framework_reader/prompts/*.md` | 三个提示词，带 `PROMPT_VERSIONS` |
| `src/framework_reader/cli/interview.py` | 访谈循环与 `$EDITOR` 确认 |
| `content/llm_providers.yaml` | 厂商预设 |
| `content/golden/NIST-CSF-2.0/*.yaml` | 3 条手写黄金样例 |

修改：`src/framework_reader/cli/main.py`（挂新命令）、`src/framework_reader/query/api.py`（`list_controls` / `interpretation`）、`src/framework_reader/pack/build.py`、`src/framework_reader/pack/db.py`、`src/framework_reader/pack/validate.py`、`pyproject.toml`、`README.md`、`Makefile`、`docs/superpowers/specs/2026-08-19-framework-reader-design.md`（Task 19 回改五处）。

---

### Task 1: 解读 schema 与状态机

**Files:**
- Create: `src/framework_reader/interpret/__init__.py`（空文件）
- Create: `src/framework_reader/interpret/model.py`
- Test: `tests/interpret/test_model.py`

**Interfaces:**
- Consumes: 无
- Produces: `Basis`、`InterpretationState`、`Field`、`Question`、`RawAnswer`、`InterviewRecord`、`ModelRef`、`InterpretationProvenance`、`Interpretation`、`DRAFTED_FIELDS`、`DIFFERENTIATING_FIELDS`、`ALL_FIELDS`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_model.py`：

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from framework_reader.interpret.model import (
    ALL_FIELDS,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    InterviewRecord,
    ModelRef,
    Question,
    RawAnswer,
)


def _fields(**overrides: Field) -> dict[str, Field]:
    base = {name: Field(value="草稿", basis=Basis.INFERRED) for name in DRAFTED_FIELDS}
    base["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for name in DIFFERENTIATING_FIELDS:
        base[name] = Field(value=None, basis=Basis.PRACTITIONER)
    base["auditor_asks"] = Field(value=None, basis=Basis.PRACTITIONER)
    base.update(overrides)
    return base


def _interp(**kw) -> Interpretation:
    payload = dict(
        control_id="NIST-CSF-2.0:GV.SC-07",
        fields=_fields(),
        interview=InterviewRecord(),
        provenance=InterpretationProvenance(),
    )
    payload.update(kw)
    return Interpretation(**payload)


def test_seven_fields_exactly():
    """spec §3.4 的七个字段，不多不少。"""
    assert set(ALL_FIELDS) == set(DRAFTED_FIELDS) | set(DIFFERENTIATING_FIELDS)
    assert len(ALL_FIELDS) == 7
    assert set(DIFFERENTIATING_FIELDS) == {"common_myth", "auditor_asks", "regional_note"}


def test_locale_defaults_to_zh_cn():
    assert _interp().locale == "zh-CN"


def test_new_interpretation_starts_as_draft():
    assert _interp().state is InterpretationState.DRAFT


def test_differentiating_field_must_be_practitioner_sourced():
    """AI 不得为这三个字段供稿——basis 写成 inferred 即建模错误。W2 spec §1 D1"""
    with pytest.raises(ValidationError):
        _interp(fields=_fields(common_myth=Field(value="x", basis=Basis.INFERRED)))


def test_empty_differentiating_field_is_allowed():
    """留空是信号，不是缺陷。W2 spec §2.2"""
    interp = _interp(fields=_fields(regional_note=Field(value=None, basis=Basis.PRACTITIONER)))
    assert interp.fields["regional_note"].value is None


def test_confirmed_requires_a_human_signature():
    with pytest.raises(ValidationError):
        _interp(state=InterpretationState.CONFIRMED)


def test_ai_may_not_sign():
    """主 spec §5：禁止直接落库。签字人不得是模型。"""
    with pytest.raises(ValidationError):
        _interp(
            state=InterpretationState.CONFIRMED,
            provenance=InterpretationProvenance(
                confirmed_by="ai:claude-opus-5",
                confirmed_at=datetime.now(timezone.utc),
            ),
        )


def test_confirmed_with_human_signature_is_valid():
    interp = _interp(
        state=InterpretationState.CONFIRMED,
        provenance=InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        ),
    )
    assert interp.state is InterpretationState.CONFIRMED


def test_missing_field_is_rejected():
    incomplete = _fields()
    del incomplete["evidence"]
    with pytest.raises(ValidationError):
        _interp(fields=incomplete)


def test_interview_record_holds_questions_and_verbatim_answers():
    record = InterviewRecord(
        questions=[Question(n=1, kind="fixed", text="最常见的误解是什么？")],
        raw=[RawAnswer(n=1, text="他们以为有张权限表就行")],
    )
    assert record.raw[0].text == "他们以为有张权限表就行"


def test_model_ref_records_provider_model_and_prompt_version():
    """换厂商等于换了生产条件，三样都要留痕。W2 spec §3.4②"""
    ref = ModelRef(provider="deepseek", model="deepseek-chat", prompt_version="2026.08-x1")
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "deepseek", "deepseek-chat", "2026.08-x1"
    )
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_model.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'framework_reader.interpret'`

- [x] **Step 3: 写最小实现**

`src/framework_reader/interpret/__init__.py`：空文件。

`src/framework_reader/interpret/model.py`：

```python
"""解读的 schema 与状态机。W2 spec §4.2、§4.3；主 spec §3.4"""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, model_validator

# AI 可以起草的四个字段。
DRAFTED_FIELDS = ("intent", "plain_zh", "practice", "evidence")
# AI 不得起草的三个字段——收费理由所在。W2 spec §1 D1
DIFFERENTIATING_FIELDS = ("common_myth", "auditor_asks", "regional_note")
ALL_FIELDS = DRAFTED_FIELDS + DIFFERENTIATING_FIELDS


class Basis(str, Enum):
    """该表述的依据。主 spec §3.4 + W2 spec §2.4"""

    QUOTE = "quote"                # 依据原文某句，值形如 quote:<定位>
    INFERRED = "inferred"          # 模型推断
    PRACTITIONER = "practitioner"  # 作者的从业经验


class InterpretationState(str, Enum):
    DRAFT = "draft"                # 起草器跑完，差异化字段为空
    INTERVIEWED = "interviewed"    # raw 已存且抽取器跑完，未签字
    CONFIRMED = "confirmed"        # 作者签字，唯一能进构建的状态


FieldValue = str | list[str] | dict[str, str] | None


class Field(BaseModel):
    value: FieldValue = None
    basis: Basis


class Question(BaseModel):
    n: int
    kind: Literal["fixed", "adaptive"]
    text: str


class RawAnswer(BaseModel):
    n: int
    text: str


class InterviewRecord(BaseModel):
    questions: list[Question] = []
    raw: list[RawAnswer] = []


class ModelRef(BaseModel):
    provider: str
    model: str
    prompt_version: str


class InterpretationProvenance(BaseModel):
    drafter: ModelRef | None = None
    extractor: ModelRef | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Interpretation(BaseModel):
    control_id: str
    locale: str = "zh-CN"
    state: InterpretationState = InterpretationState.DRAFT
    fields: dict[str, Field]
    interview: InterviewRecord = InterviewRecord()
    provenance: InterpretationProvenance = InterpretationProvenance()

    @model_validator(mode="after")
    def _check_invariants(self) -> "Interpretation":
        missing = set(ALL_FIELDS) - set(self.fields)
        extra = set(self.fields) - set(ALL_FIELDS)
        if missing or extra:
            raise ValueError(f"字段集必须恰好是七个；缺 {sorted(missing)}，多 {sorted(extra)}")

        for name in DIFFERENTIATING_FIELDS:
            if self.fields[name].basis is not Basis.PRACTITIONER:
                raise ValueError(
                    f"{name} 是差异化字段，basis 必须是 practitioner——"
                    f"AI 不得为它供稿（W2 spec §1 D1）"
                )

        if self.state is InterpretationState.CONFIRMED:
            signer = (self.provenance.confirmed_by or "").strip()
            if not signer or self.provenance.confirmed_at is None:
                raise ValueError("confirmed 必须记录 confirmed_by 与 confirmed_at")
            if signer.startswith("ai:") or signer.startswith("model:"):
                raise ValueError(f"AI 不能签字：confirmed_by={signer}（主 spec §5）")
        return self
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/interpret/test_model.py -v`
Expected: 12 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/interpret/ tests/interpret/
git commit -m "feat(interpret): 解读 schema——差异化字段禁止 AI 供稿，AI 不能签字"
```

---

### Task 2: YAML 存储层

**Files:**
- Create: `src/framework_reader/interpret/store.py`
- Test: `tests/interpret/test_store.py`

**Interfaces:**
- Consumes: Task 1 的 `Interpretation`、`RawAnswer`、`InterpretationState`
- Produces: `InterpretationStore(root: Path)`，方法 `path_for(control_id) -> Path`、`save(interp) -> Path`、`load(control_id) -> Interpretation`、`exists(control_id) -> bool`、`iter_all() -> Iterator[Interpretation]`、`by_state(state) -> list[Interpretation]`、`append_raw(control_id, n, text) -> None`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_store.py`：

```python
import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
)
from framework_reader.interpret.store import InterpretationStore


def _interp(control_id: str = "NIST-CSF-2.0:GV.SC-07") -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    return Interpretation(control_id=control_id, fields=fields)


def test_path_is_one_file_per_control_under_framework_dir(tmp_path):
    store = InterpretationStore(tmp_path)
    path = store.path_for("NIST-CSF-2.0:GV.SC-07")
    assert path == tmp_path / "NIST-CSF-2.0" / "GV.SC-07.yaml"


def test_round_trip_preserves_everything(tmp_path):
    store = InterpretationStore(tmp_path)
    original = _interp()
    store.save(original)
    assert store.load("NIST-CSF-2.0:GV.SC-07") == original


def test_yaml_is_human_readable_utf8(tmp_path):
    """内容进 Git，作者要能直接读 diff——不许 ASCII 转义。"""
    store = InterpretationStore(tmp_path)
    interp = _interp()
    interp.fields["intent"] = Field(value="供应链风险不是签一次合同就完", basis=Basis.INFERRED)
    text = store.save(interp).read_text(encoding="utf-8")
    assert "供应链风险不是签一次合同就完" in text
    assert "\\u" not in text


def test_append_raw_persists_immediately(tmp_path):
    """作者说过的话，答完一问就落盘，不等三问答完。W2 spec §6"""
    store = InterpretationStore(tmp_path)
    store.save(_interp())
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="他们以为有张权限表就行")
    reloaded = store.load("NIST-CSF-2.0:GV.SC-07")
    assert [(r.n, r.text) for r in reloaded.interview.raw] == [
        (1, "他们以为有张权限表就行")
    ]


def test_append_raw_is_idempotent_per_question(tmp_path):
    """重答同一问覆盖该问，不追加第二条——续跑不能产生重复。"""
    store = InterpretationStore(tmp_path)
    store.save(_interp())
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="第一版")
    store.append_raw("NIST-CSF-2.0:GV.SC-07", n=1, text="改口后的版本")
    raw = store.load("NIST-CSF-2.0:GV.SC-07").interview.raw
    assert [(r.n, r.text) for r in raw] == [(1, "改口后的版本")]


def test_by_state_filters(tmp_path):
    store = InterpretationStore(tmp_path)
    store.save(_interp("NIST-CSF-2.0:GV.SC-07"))
    other = _interp("NIST-CSF-2.0:PR.AA-05")
    other.state = InterpretationState.INTERVIEWED
    store.save(other)
    drafts = store.by_state(InterpretationState.DRAFT)
    assert [i.control_id for i in drafts] == ["NIST-CSF-2.0:GV.SC-07"]


def test_iter_all_is_sorted_and_stable(tmp_path):
    store = InterpretationStore(tmp_path)
    for cid in ("NIST-CSF-2.0:PR.AA-05", "NIST-CSF-2.0:GV.SC-07"):
        store.save(_interp(cid))
    assert [i.control_id for i in store.iter_all()] == [
        "NIST-CSF-2.0:GV.SC-07", "NIST-CSF-2.0:PR.AA-05",
    ]


def test_load_missing_control_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        InterpretationStore(tmp_path).load("NIST-CSF-2.0:NOPE")


def test_save_is_atomic(tmp_path):
    """写盘中途崩不能留下半个文件——先写临时文件再 replace。"""
    store = InterpretationStore(tmp_path)
    path = store.save(_interp())
    assert path.exists()
    assert not list(path.parent.glob("*.tmp"))
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_store.py -v`
Expected: FAIL —— `ImportError: cannot import name 'InterpretationStore'`

- [x] **Step 3: 写最小实现**

`src/framework_reader/interpret/store.py`：

```python
"""解读的 YAML 存储。主 spec §9：内容以 YAML 存于 Git，SQLite 是构建产物。"""
import os
from collections.abc import Iterator
from pathlib import Path

import yaml

from framework_reader.interpret.model import (
    Interpretation,
    InterpretationState,
    RawAnswer,
)

DEFAULT_ROOT = Path("content/interpretations")


class InterpretationStore:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, control_id: str) -> Path:
        framework, local = control_id.split(":", 1)
        return self.root / framework / f"{local}.yaml"

    def exists(self, control_id: str) -> bool:
        return self.path_for(control_id).exists()

    def save(self, interp: Interpretation) -> Path:
        path = self.path_for(interp.control_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = interp.model_dump(mode="json", exclude_none=False)
        text = yaml.safe_dump(
            payload, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, control_id: str) -> Interpretation:
        path = self.path_for(control_id)
        if not path.exists():
            raise FileNotFoundError(f"没有 {control_id} 的解读文件：{path}")
        return Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))

    def iter_all(self) -> Iterator[Interpretation]:
        for path in sorted(self.root.rglob("*.yaml")):
            yield Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))

    def by_state(self, state: InterpretationState) -> list[Interpretation]:
        return [i for i in self.iter_all() if i.state is state]

    def append_raw(self, control_id: str, n: int, text: str) -> None:
        """答完一问立刻落盘。同一问重答则覆盖，不追加。W2 spec §6"""
        interp = self.load(control_id)
        kept = [r for r in interp.interview.raw if r.n != n]
        kept.append(RawAnswer(n=n, text=text))
        interp.interview.raw = sorted(kept, key=lambda r: r.n)
        self.save(interp)
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/interpret/test_store.py -v`
Expected: 9 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/interpret/store.py tests/interpret/test_store.py
git commit -m "feat(interpret): YAML 存储层，答完一问即原子落盘"
```

---

### Task 3: 3 条手写黄金样例与 `fr golden validate`

**这是本周唯一一个由人执行的 task，且必须在 Task 8–11（起草器/提问器/抽取器/lint）之前完成。**
黄金样例是管线的验收标准；若由 AI 起草再人改，等于拿管线的输出当管线的验收标准。（W2 spec §1.3）

**Files:**
- Create: `content/golden/NIST-CSF-2.0/GV.SC-07.yaml`（人手写）
- Create: `content/golden/NIST-CSF-2.0/PR.AA-05.yaml`（人手写）
- Create: `content/golden/NIST-CSF-2.0/GV.RM-02.yaml`（人手写）
- Modify: `src/framework_reader/cli/main.py`
- Test: `tests/interpret/test_golden.py`

**Interfaces:**
- Consumes: Task 1 `Interpretation`、Task 2 `InterpretationStore`
- Produces: CLI `fr golden validate`；常量 `GOLDEN_ROOT = Path("content/golden")`、`GOLDEN_CONTROLS: tuple[str, ...]`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_golden.py`：

```python
from pathlib import Path

import pytest

from framework_reader.interpret.golden import GOLDEN_CONTROLS, GOLDEN_ROOT, load_golden
from framework_reader.interpret.model import Basis, DIFFERENTIATING_FIELDS, InterpretationState


def test_three_golden_controls_are_the_ones_the_spec_names():
    assert GOLDEN_CONTROLS == (
        "NIST-CSF-2.0:GV.SC-07",
        "NIST-CSF-2.0:PR.AA-05",
        "NIST-CSF-2.0:GV.RM-02",
    )


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_file_exists_and_parses(control_id):
    assert load_golden(control_id).control_id == control_id


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_is_confirmed_and_signed_by_a_human(control_id):
    golden = load_golden(control_id)
    assert golden.state is InterpretationState.CONFIRMED
    assert golden.provenance.confirmed_by
    assert not golden.provenance.confirmed_by.startswith("ai:")


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_has_no_model_provenance(control_id):
    """黄金样例零 AI 参与——一旦有 drafter/extractor 记录，它就不是尺子了。W2 spec §1.3"""
    golden = load_golden(control_id)
    assert golden.provenance.drafter is None
    assert golden.provenance.extractor is None


@pytest.mark.parametrize("control_id", GOLDEN_CONTROLS)
def test_golden_fills_at_least_two_differentiating_fields(control_id):
    """尺子本身必须有刻度：三个差异化字段至少两个有内容。"""
    golden = load_golden(control_id)
    filled = [n for n in DIFFERENTIATING_FIELDS if golden.fields[n].value]
    assert len(filled) >= 2, f"{control_id} 只填了 {filled}"
    for name in DIFFERENTIATING_FIELDS:
        assert golden.fields[name].basis is Basis.PRACTITIONER


def test_golden_root_is_separate_from_production():
    assert GOLDEN_ROOT == Path("content/golden")
    assert GOLDEN_ROOT != Path("content/interpretations")
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_golden.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.golden`

- [x] **Step 3: 写最小实现**

`src/framework_reader/interpret/golden.py`：

```python
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
```

- [x] **Step 4: 人工撰写 3 条黄金样例**

**这一步由作者本人完成，不得由 AI 代笔、不得由 AI 起草后修改。**

对每条控制，先用 `fr show <control_id>` 看 CSF outcome 原文与 L1 邻居，然后手写下面这个文件。
`GV.SC-07` 的模板（另两条同构，只换 `control_id`）：

```yaml
control_id: NIST-CSF-2.0:GV.SC-07
locale: zh-CN
state: confirmed
fields:
  intent:
    value: "（这条到底在防什么风险——不是翻译条文）"
    basis: practitioner
  plain_zh:
    value: "（用大白话说它要你干什么）"
    basis: practitioner
  practice:
    value:
      "1": "（成熟度 1 档：最低限度怎么做）"
      "2": "（2 档）"
      "3": "（3 档）"
    basis: practitioner
  evidence:
    value: "（审计员通常要看什么形态的东西）"
    basis: practitioner
  common_myth:
    value: "（中文团队对这条最常见的误解）"
    basis: practitioner
  auditor_asks:
    value:
      - "（审计员会追问的第一句）"
      - "（第二句）"
    basis: practitioner
  regional_note:
    value: "（欧洲/美国审计员对这条的松紧差异；确实没有就写 null）"
    basis: practitioner
interview:
  questions: []
  raw: []
provenance:
  drafter: null
  extractor: null
  confirmed_by: jc
  confirmed_at: 2026-08-21T10:00:00+08:00
```

注意：黄金样例里**七个字段的 `basis` 全部是 `practitioner`**（整份都是人写的），这与生产文件
不同——生产文件的四个非差异化字段是 `inferred`。

- [x] **Step 5: 挂 CLI 命令**

`src/framework_reader/cli/main.py` 追加：

```python
@app.command("golden")
def golden(action: str = typer.Argument(..., help="validate")) -> None:
    """黄金样例相关操作。"""
    from framework_reader.interpret.golden import GOLDEN_CONTROLS, load_golden

    if action != "validate":
        typer.echo(f"未知操作：{action}")
        raise typer.Exit(2)
    for control_id in GOLDEN_CONTROLS:
        golden = load_golden(control_id)
        filled = [
            name for name in ("common_myth", "auditor_asks", "regional_note")
            if golden.fields[name].value
        ]
        typer.echo(f"{control_id}  签字={golden.provenance.confirmed_by}  差异化字段已填={filled}")
```

- [x] **Step 6: 运行测试确认通过**

Run: `pytest tests/interpret/test_golden.py -v && fr golden validate`
Expected: 全部 PASS；`fr golden validate` 打印三行，每行 `已填` 至少两个字段

- [x] **Step 7: 提交**

```bash
git add content/golden/ src/framework_reader/interpret/golden.py \
        src/framework_reader/cli/main.py tests/interpret/test_golden.py
git commit -m "feat(golden): 3 条手写黄金样例与校验命令——零 AI 参与"
```

---

### Task 4: `LLMClient` 协议、`FakeClient` 与出口红线

出口红线是本 task 的重点：**Tier C/D 原文不得进入任何模型调用的 payload**（W2 spec §3.4③）。
W2 只跑 CSF（公共领域），此刻加成本近乎为零；W4–W5 做 ISO 时再想起来就晚了。

**Files:**
- Create: `src/framework_reader/llm/__init__.py`（空文件）
- Create: `src/framework_reader/llm/client.py`
- Create: `src/framework_reader/llm/guard.py`
- Test: `tests/llm/test_client.py`
- Test: `tests/llm/test_guard.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `client.py`：`Message`、`LLMClient`（Protocol，方法 `complete(system: str, messages: list[Message], *, model: str, max_tokens: int) -> str`）、`FakeClient(responses: list[str])`（属性 `calls: list[dict]`）
  - `guard.py`：`OutboundTextError`、`PayloadGuard(forbidden: Sequence[str], min_chunk: int = 24)`（方法 `check(*texts: str) -> None`）、`forbidden_texts_from_db(conn) -> list[str]`、`GuardedClient(inner, guard)`

- [x] **Step 1: 写失败测试**

`tests/llm/test_client.py`：

```python
import pytest

from framework_reader.llm.client import FakeClient, Message


def test_fake_client_returns_queued_responses_in_order():
    client = FakeClient(["第一条", "第二条"])
    assert client.complete("sys", [Message(role="user", content="a")], model="m") == "第一条"
    assert client.complete("sys", [Message(role="user", content="b")], model="m") == "第二条"


def test_fake_client_records_calls_for_assertions():
    client = FakeClient(["x"])
    client.complete("你是助手", [Message(role="user", content="问题")], model="m", max_tokens=99)
    assert client.calls == [{
        "system": "你是助手",
        "messages": [{"role": "user", "content": "问题"}],
        "model": "m",
        "max_tokens": 99,
    }]


def test_fake_client_runs_out_loudly():
    """测试夹具耗尽必须炸，不能悄悄返回空串让断言假通过。"""
    client = FakeClient(["only"])
    client.complete("s", [], model="m")
    with pytest.raises(AssertionError):
        client.complete("s", [], model="m")
```

`tests/llm/test_guard.py`：

```python
import sqlite3

import pytest

from framework_reader.llm.client import FakeClient, Message
from framework_reader.llm.guard import (
    GuardedClient,
    OutboundTextError,
    PayloadGuard,
    forbidden_texts_from_db,
)

ISO_BODY = "组织应定义并实施过程以管理与供方相关的信息安全风险并按约定频率复核"


def test_guard_blocks_forbidden_text_in_user_message():
    guard = PayloadGuard([ISO_BODY])
    with pytest.raises(OutboundTextError):
        guard.check(f"请解读这段：{ISO_BODY}")


def test_guard_blocks_forbidden_text_in_system_prompt():
    guard = PayloadGuard([ISO_BODY])
    with pytest.raises(OutboundTextError):
        guard.check(ISO_BODY, "无害的用户消息")


def test_guard_allows_short_incidental_overlap():
    """「组织应定义」这类短语到处都是，按整段比对才有意义。"""
    guard = PayloadGuard([ISO_BODY], min_chunk=24)
    guard.check("组织应定义安全职责")


def test_guard_ignores_forbidden_entries_shorter_than_min_chunk():
    guard = PayloadGuard(["短句"], min_chunk=24)
    guard.check("这里出现了短句也不该报")


def test_guarded_client_raises_before_calling_inner():
    inner = FakeClient(["不该被用到"])
    client = GuardedClient(inner, PayloadGuard([ISO_BODY]))
    with pytest.raises(OutboundTextError):
        client.complete("sys", [Message(role="user", content=ISO_BODY)], model="m")
    assert inner.calls == [], "红线断言必须在调用发生之前拦住"


def test_guarded_client_passes_clean_payload_through():
    inner = FakeClient(["ok"])
    client = GuardedClient(inner, PayloadGuard([ISO_BODY]))
    out = client.complete("sys", [Message(role="user", content="CSF 是公共领域")], model="m")
    assert out == "ok"


def test_forbidden_texts_come_from_the_original_text_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE original_text (control_id TEXT, locale TEXT, body TEXT)"
    )
    conn.execute(
        "INSERT INTO original_text VALUES (?, ?, ?)",
        ("ISO-27002-2022:A.5.22", "zh-CN", ISO_BODY),
    )
    assert forbidden_texts_from_db(conn) == [ISO_BODY]


def test_empty_original_text_table_yields_no_forbidden_texts():
    """构建产物里该表恒为空——用户本地注入后才有内容。主 spec §3.2②"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE original_text (control_id TEXT, locale TEXT, body TEXT)")
    assert forbidden_texts_from_db(conn) == []
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/llm -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.llm`

- [x] **Step 3: 写最小实现**

`src/framework_reader/llm/__init__.py`：空文件。

`src/framework_reader/llm/client.py`：

```python
"""模型客户端协议。W2 spec §3.1

所有适配器实现同一个 complete()；调用方永远不直接碰厂商 SDK。
"""
from typing import Literal, Protocol

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str: ...


class FakeClient:
    """测试用。公有 CI 零网络，全部模型调用走它。W2 spec §7"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        self.calls.append({
            "system": system,
            "messages": [m.model_dump() for m in messages],
            "model": model,
            "max_tokens": max_tokens,
        })
        assert self._responses, "FakeClient 预置响应已耗尽——测试夹具与被测代码不一致"
        return self._responses.pop(0)
```

`src/framework_reader/llm/guard.py`：

```python
"""出口红线：Tier C/D 原文不得进入任何模型调用的 payload。W2 spec §3.4③

与主 spec §10.A 的两条红线同级：抛异常，不重试、不降级、不降为 warn。
"""
import sqlite3
from collections.abc import Sequence

from framework_reader.llm.client import LLMClient, Message


class OutboundTextError(Exception):
    """受版权原文即将出圈。构建/运行必须中止。"""


class PayloadGuard:
    def __init__(self, forbidden: Sequence[str], min_chunk: int = 24) -> None:
        # 短片段（「组织应定义」之类）在任何中文文本里都会撞上，按整段比对才有意义。
        self._forbidden = [t.strip() for t in forbidden if len(t.strip()) >= min_chunk]

    def check(self, *texts: str) -> None:
        for text in texts:
            for body in self._forbidden:
                if body in text:
                    raise OutboundTextError(
                        f"payload 含受版权原文（前 20 字：{body[:20]}…）——"
                        f"Tier C/D 原文不得发给任何模型厂商"
                    )


def forbidden_texts_from_db(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT body FROM original_text").fetchall()]


class GuardedClient:
    """唯一的出网路径。registry 组装的每个 client 都被它包住。"""

    def __init__(self, inner: LLMClient, guard: PayloadGuard) -> None:
        self._inner = inner
        self._guard = guard

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        self._guard.check(system, *(m.content for m in messages))
        return self._inner.complete(system, messages, model=model, max_tokens=max_tokens)
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/llm -v`
Expected: 11 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/llm/ tests/llm/
git commit -m "feat(llm): 客户端协议与出口红线——Tier C/D 原文不得出圈"
```

---

### Task 5: OpenAI 兼容适配器

国内厂商普遍提供 OpenAI 兼容端点，本适配器一个吃掉 `deepseek` / `qwen` / `glm` / `kimi` /
`doubao` / `hunyuan` / `minimax` / `baichuan` / `siliconflow` / `openai`。

**测试只验请求形状，不发真实请求**——活连通归 `fr llm check`（Task 7），由人手工跑。

**Files:**
- Create: `src/framework_reader/llm/openai_compat.py`
- Test: `tests/llm/test_openai_compat.py`

**Interfaces:**
- Consumes: Task 4 的 `Message`
- Produces: `OpenAICompatClient(base_url: str, api_key: str, *, http_post: Callable[[str, dict, dict], dict] | None = None)`，方法 `complete(...) -> str`；模块级 `build_payload(system, messages, model, max_tokens) -> dict`

- [x] **Step 1: 写失败测试**

`tests/llm/test_openai_compat.py`：

```python
import pytest

from framework_reader.llm.client import Message
from framework_reader.llm.openai_compat import OpenAICompatClient, build_payload


def test_system_prompt_becomes_the_first_message():
    payload = build_payload("你是助手", [Message(role="user", content="问题")], "deepseek-chat", 512)
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1] == {"role": "user", "content": "问题"}
    assert payload["model"] == "deepseek-chat"
    assert payload["max_tokens"] == 512


def test_empty_system_prompt_is_omitted():
    payload = build_payload("", [Message(role="user", content="问题")], "m", 10)
    assert payload["messages"] == [{"role": "user", "content": "问题"}]


def _recorder(captured: list):
    def post(url: str, headers: dict, payload: dict) -> dict:
        captured.append({"url": url, "headers": headers, "payload": payload})
        return {"choices": [{"message": {"content": "模型回答"}}]}
    return post


def test_request_goes_to_chat_completions_with_bearer_key():
    captured: list = []
    client = OpenAICompatClient(
        "https://api.deepseek.com", "sk-test", http_post=_recorder(captured)
    )
    out = client.complete("sys", [Message(role="user", content="hi")], model="deepseek-chat")
    assert out == "模型回答"
    assert captured[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_trailing_slash_in_base_url_does_not_double_up():
    captured: list = []
    client = OpenAICompatClient(
        "https://api.moonshot.cn/v1/", "k", http_post=_recorder(captured)
    )
    client.complete("s", [Message(role="user", content="x")], model="kimi-latest")
    assert captured[0]["url"] == "https://api.moonshot.cn/v1/chat/completions"


def test_unexpected_response_shape_raises_instead_of_returning_empty():
    def bad_post(url, headers, payload):
        return {"error": {"message": "quota exceeded"}}

    client = OpenAICompatClient("https://x", "k", http_post=bad_post)
    with pytest.raises(RuntimeError, match="quota exceeded"):
        client.complete("s", [Message(role="user", content="x")], model="m")
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/llm/test_openai_compat.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.llm.openai_compat`

- [x] **Step 3: 写最小实现**

`src/framework_reader/llm/openai_compat.py`：

```python
"""OpenAI 兼容适配器。W2 spec §3.1

一个适配器覆盖 deepseek / qwen / glm / kimi / doubao / hunyuan / minimax /
baichuan / siliconflow / openai —— 它们都提供 /chat/completions。
"""
import json
from collections.abc import Callable

from framework_reader.llm.client import Message

HttpPost = Callable[[str, dict, dict], dict]


def build_payload(
    system: str, messages: list[Message], model: str, max_tokens: int
) -> dict:
    body: list[dict] = []
    if system.strip():
        body.append({"role": "system", "content": system})
    body.extend(m.model_dump() for m in messages)
    return {"model": model, "messages": body, "max_tokens": max_tokens}


def _default_post(url: str, headers: dict, payload: dict) -> dict:
    import httpx

    resp = httpx.post(url, headers=headers, json=payload, timeout=120.0)
    resp.raise_for_status()
    return resp.json()


class OpenAICompatClient:
    def __init__(
        self, base_url: str, api_key: str, *, http_post: HttpPost | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._post = http_post or _default_post

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        payload = build_payload(system, messages, model, max_tokens)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = self._post(f"{self._base_url}/chat/completions", headers, payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"厂商返回了预期外的结构：{json.dumps(data, ensure_ascii=False)[:300]}"
            ) from exc
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/llm/test_openai_compat.py -v`
Expected: 5 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/llm/openai_compat.py tests/llm/test_openai_compat.py
git commit -m "feat(llm): OpenAI 兼容适配器，覆盖国内主流厂商"
```

---

### Task 6: Anthropic 原生适配器

唯一支持**显式** prompt caching 的厂商。缓存是 best-effort：接口不得含「必须命中」的假设。

**Files:**
- Create: `src/framework_reader/llm/anthropic_adapter.py`
- Test: `tests/llm/test_anthropic_adapter.py`

**Interfaces:**
- Consumes: Task 4 的 `Message`
- Produces: `AnthropicClient(api_key: str, *, send: Callable[[dict], dict] | None = None, cache_system: bool = True)`；模块级 `build_payload(system, messages, model, max_tokens, cache_system) -> dict`

- [x] **Step 1: 写失败测试**

`tests/llm/test_anthropic_adapter.py`：

```python
import pytest

from framework_reader.llm.anthropic_adapter import AnthropicClient, build_payload
from framework_reader.llm.client import Message


def test_system_is_a_top_level_block_not_a_message():
    payload = build_payload("你是助手", [Message(role="user", content="问题")], "claude-opus-5", 512, True)
    assert payload["messages"] == [{"role": "user", "content": "问题"}]
    assert payload["system"][0]["text"] == "你是助手"


def test_system_block_carries_cache_control_when_enabled():
    """固定前缀（system + 黄金样例）缓存后成本与延迟都能砍掉大半。W2 spec §3.4①"""
    payload = build_payload("长前缀", [], "claude-opus-5", 10, True)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_control_can_be_turned_off():
    payload = build_payload("长前缀", [], "claude-opus-5", 10, False)
    assert "cache_control" not in payload["system"][0]


def test_empty_system_omits_the_block_entirely():
    payload = build_payload("", [Message(role="user", content="x")], "m", 10, True)
    assert "system" not in payload


def test_complete_returns_concatenated_text_blocks():
    def send(payload: dict) -> dict:
        return {"content": [{"type": "text", "text": "前半"}, {"type": "text", "text": "后半"}]}

    client = AnthropicClient("sk-ant-test", send=send)
    assert client.complete("s", [Message(role="user", content="x")], model="m") == "前半后半"


def test_unexpected_response_shape_raises():
    client = AnthropicClient("k", send=lambda payload: {"error": {"message": "overloaded"}})
    with pytest.raises(RuntimeError, match="overloaded"):
        client.complete("s", [Message(role="user", content="x")], model="m")
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/llm/test_anthropic_adapter.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.llm.anthropic_adapter`

- [x] **Step 3: 写最小实现**

`src/framework_reader/llm/anthropic_adapter.py`：

```python
"""Anthropic 原生适配器。W2 spec §3.1、§3.4①

唯一支持显式 prompt caching 的厂商；缓存命中与否不影响正确性。
"""
import json
from collections.abc import Callable

from framework_reader.llm.client import Message

Send = Callable[[dict], dict]


def build_payload(
    system: str,
    messages: list[Message],
    model: str,
    max_tokens: int,
    cache_system: bool,
) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [m.model_dump() for m in messages],
    }
    if system.strip():
        block: dict = {"type": "text", "text": system}
        if cache_system:
            block["cache_control"] = {"type": "ephemeral"}
        payload["system"] = [block]
    return payload


def _default_send(payload: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    return client.messages.create(**payload).model_dump()


class AnthropicClient:
    def __init__(
        self, api_key: str, *, send: Send | None = None, cache_system: bool = True
    ) -> None:
        self._api_key = api_key
        self._cache_system = cache_system
        self._send = send or self._make_default_send()

    def _make_default_send(self) -> Send:
        def send(payload: dict) -> dict:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            return client.messages.create(**payload).model_dump()

        return send

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        data = self._send(
            build_payload(system, messages, model, max_tokens, self._cache_system)
        )
        blocks = data.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise RuntimeError(
                f"厂商返回了预期外的结构：{json.dumps(data, ensure_ascii=False)[:300]}"
            )
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/llm/test_anthropic_adapter.py -v`
Expected: 6 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/llm/anthropic_adapter.py tests/llm/test_anthropic_adapter.py
git commit -m "feat(llm): Anthropic 原生适配器，显式 prompt caching 为 best-effort"
```

---

### Task 7: 厂商预设注册表与 `fr llm check`

四个调用点各自指定厂商（W2 spec §3.3）。`registry` 是唯一组装 client 的地方，且**每个
client 都被 `GuardedClient` 包住**——这保证出网只有一条路径。

**Files:**
- Create: `content/llm_providers.yaml`
- Create: `src/framework_reader/llm/registry.py`
- Modify: `src/framework_reader/cli/main.py`
- Test: `tests/llm/test_registry.py`

**Interfaces:**
- Consumes: Task 4 `GuardedClient` / `PayloadGuard`、Task 5 `OpenAICompatClient`、Task 6 `AnthropicClient`
- Produces: `ProviderPreset`、`RoleConfig`、`MissingApiKeyError`、`UnknownProviderError`、`LLMRegistry`（`load(path)`、`preset(id)`、`role(name)`、`build(role, guard, key_lookup=os.environ.get)`）、`DEFAULT_REGISTRY_PATH`

- [x] **Step 1: 写预设文件**

`content/llm_providers.yaml`：

```yaml
# 模型厂商预设。W2 spec §3.2
#
# 端点会漂：配好 key 后跑 `fr llm check` 逐个验活，验不通的标灰保留、不删除。
# kind 只有两种：anthropic（原生，唯一支持显式 prompt caching）与 openai_compat。
providers:
  - id: anthropic
    kind: anthropic
    base_url: ""
    api_key_env: ANTHROPIC_API_KEY
    default_model: claude-opus-5
    explicit_cache: true
    note: 唯一支持显式 prompt caching

  - id: deepseek
    kind: openai_compat
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    default_model: deepseek-chat
    explicit_cache: false
    note: 自动上下文缓存

  - id: qwen
    kind: openai_compat
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: DASHSCOPE_API_KEY
    default_model: qwen-max
    explicit_cache: false
    note: 阿里百炼；隐式缓存

  - id: glm
    kind: openai_compat
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key_env: ZHIPU_API_KEY
    default_model: glm-4-plus
    explicit_cache: false
    note: 智谱

  - id: kimi
    kind: openai_compat
    base_url: https://api.moonshot.cn/v1
    api_key_env: MOONSHOT_API_KEY
    default_model: kimi-latest
    explicit_cache: false
    note: 月之暗面；长上下文

  - id: doubao
    kind: openai_compat
    base_url: https://ark.cn-beijing.volces.com/api/v3
    api_key_env: ARK_API_KEY
    default_model: doubao-pro-32k
    explicit_cache: false
    note: 火山方舟

  - id: hunyuan
    kind: openai_compat
    base_url: https://api.hunyuan.cloud.tencent.com/v1
    api_key_env: HUNYUAN_API_KEY
    default_model: hunyuan-turbo
    explicit_cache: false
    note: 腾讯

  - id: minimax
    kind: openai_compat
    base_url: https://api.minimax.chat/v1
    api_key_env: MINIMAX_API_KEY
    default_model: abab6.5s-chat
    explicit_cache: false
    note: ""

  - id: baichuan
    kind: openai_compat
    base_url: https://api.baichuan-ai.com/v1
    api_key_env: BAICHUAN_API_KEY
    default_model: Baichuan4
    explicit_cache: false
    note: ""

  - id: siliconflow
    kind: openai_compat
    base_url: https://api.siliconflow.cn/v1
    api_key_env: SILICONFLOW_API_KEY
    default_model: deepseek-ai/DeepSeek-V3
    explicit_cache: false
    note: 聚合口，一个 key 试多个开源模型

  - id: openai
    kind: openai_compat
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    default_model: gpt-4o
    explicit_cache: false
    note: ""

# 四个调用点各自指定厂商——它们的需求不同。W2 spec §3.3
# extractor 收到的是作者原话（本产品最核心的资产），发给哪家是商业判断。
roles:
  drafter:    {provider: anthropic, model: claude-opus-5}
  questioner: {provider: deepseek,  model: deepseek-chat}
  extractor:  {provider: deepseek,  model: deepseek-chat}
```

- [x] **Step 2: 写失败测试**

`tests/llm/test_registry.py`：

```python
from pathlib import Path

import pytest

from framework_reader.llm.anthropic_adapter import AnthropicClient
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.openai_compat import OpenAICompatClient
from framework_reader.llm.registry import (
    DEFAULT_REGISTRY_PATH,
    LLMRegistry,
    MissingApiKeyError,
    UnknownProviderError,
)

REG = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-x",
    "DEEPSEEK_API_KEY": "sk-ds-x",
}.get


def test_ships_the_eleven_presets_the_spec_names():
    assert {p.id for p in REG.providers} == {
        "anthropic", "deepseek", "qwen", "glm", "kimi", "doubao",
        "hunyuan", "minimax", "baichuan", "siliconflow", "openai",
    }


def test_only_anthropic_claims_explicit_cache():
    """prompt caching 是 best-effort，其余厂商不得声称显式缓存。W2 spec §3.4①"""
    assert [p.id for p in REG.providers if p.explicit_cache] == ["anthropic"]


def test_every_preset_declares_a_key_env_and_default_model():
    for preset in REG.providers:
        assert preset.api_key_env, preset.id
        assert preset.default_model, preset.id


def test_openai_compat_presets_have_a_base_url():
    for preset in REG.providers:
        if preset.kind == "openai_compat":
            assert preset.base_url.startswith("https://"), preset.id


def test_three_roles_are_configured():
    assert {"drafter", "questioner", "extractor"} == set(REG.roles)


def test_build_wraps_every_client_in_the_guard():
    """出网只有一条路径：registry 组装的 client 一律被红线包住。W2 spec §3.4③"""
    client = REG.build("drafter", guard=PayloadGuard([]), key_lookup=KEYS)
    assert isinstance(client, GuardedClient)


def test_build_picks_the_adapter_matching_the_preset_kind():
    drafter = REG.build("drafter", guard=PayloadGuard([]), key_lookup=KEYS)
    extractor = REG.build("extractor", guard=PayloadGuard([]), key_lookup=KEYS)
    assert isinstance(drafter._inner, AnthropicClient)
    assert isinstance(extractor._inner, OpenAICompatClient)


def test_missing_api_key_fails_loudly_and_names_the_env_var():
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        REG.build("drafter", guard=PayloadGuard([]), key_lookup=lambda name: None)


def test_role_pointing_at_an_unknown_provider_is_rejected(tmp_path: Path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "providers: []\nroles:\n  drafter: {provider: nope, model: m}\n", encoding="utf-8"
    )
    with pytest.raises(UnknownProviderError, match="nope"):
        LLMRegistry.load(path).build("drafter", guard=PayloadGuard([]), key_lookup=KEYS)


def test_role_model_overrides_the_preset_default():
    assert REG.role("extractor").model == "deepseek-chat"
```

- [x] **Step 3: 运行测试确认失败**

Run: `pytest tests/llm/test_registry.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.llm.registry`

- [x] **Step 4: 写最小实现**

`src/framework_reader/llm/registry.py`：

```python
"""厂商预设与按角色组装 client。W2 spec §3.2、§3.3

这是唯一组装 client 的地方，且每个 client 都被 GuardedClient 包住——
出网只有一条路径。
"""
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from framework_reader.llm.anthropic_adapter import AnthropicClient
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.openai_compat import OpenAICompatClient

DEFAULT_REGISTRY_PATH = Path("content/llm_providers.yaml")
KeyLookup = Callable[[str], str | None]


class MissingApiKeyError(Exception):
    """预设声明的环境变量没设。"""


class UnknownProviderError(Exception):
    """role 指向了不存在的 provider id。"""


class ProviderPreset(BaseModel):
    id: str
    kind: Literal["anthropic", "openai_compat"]
    base_url: str = ""
    api_key_env: str
    default_model: str
    explicit_cache: bool = False
    note: str = ""


class RoleConfig(BaseModel):
    provider: str
    model: str


class LLMRegistry(BaseModel):
    providers: list[ProviderPreset]
    roles: dict[str, RoleConfig]

    @classmethod
    def load(cls, path: Path = DEFAULT_REGISTRY_PATH) -> "LLMRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            providers=[ProviderPreset(**p) for p in data.get("providers", [])],
            roles={k: RoleConfig(**v) for k, v in (data.get("roles") or {}).items()},
        )

    def preset(self, provider_id: str) -> ProviderPreset:
        for preset in self.providers:
            if preset.id == provider_id:
                return preset
        raise UnknownProviderError(f"预设里没有 provider：{provider_id}")

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise UnknownProviderError(f"没有配置角色：{name}")
        return self.roles[name]

    def build(
        self,
        role: str,
        *,
        guard: PayloadGuard,
        key_lookup: KeyLookup = os.environ.get,
    ) -> GuardedClient:
        cfg = self.role(role)
        preset = self.preset(cfg.provider)
        key = key_lookup(preset.api_key_env)
        if not key:
            raise MissingApiKeyError(
                f"角色 {role} 要用 {preset.id}，但环境变量 {preset.api_key_env} 没设"
            )
        if preset.kind == "anthropic":
            inner = AnthropicClient(key, cache_system=preset.explicit_cache)
        else:
            inner = OpenAICompatClient(preset.base_url, key)
        return GuardedClient(inner, guard)
```

- [x] **Step 5: 挂 `fr llm check`**

`src/framework_reader/cli/main.py` 追加。**此命令会发真实网络请求，只由人手工跑，
测试永远不得调用它**（Task 16 有断言守着）：

```python
@app.command("llm")
def llm(action: str = typer.Argument(..., help="check")) -> None:
    """厂商预设验活。会发真实请求，只手工跑。"""
    import os

    from framework_reader.llm.client import Message
    from framework_reader.llm.guard import PayloadGuard
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

    if action != "check":
        typer.echo(f"未知操作：{action}")
        raise typer.Exit(2)

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    guard = PayloadGuard([])
    for preset in registry.providers:
        if not os.environ.get(preset.api_key_env):
            typer.echo(f"{preset.id:14} 跳过（{preset.api_key_env} 未设）")
            continue
        probe = LLMRegistry(
            providers=[preset],
            roles={"probe": {"provider": preset.id, "model": preset.default_model}},
        ).build("probe", guard=guard)
        try:
            probe.complete(
                "", [Message(role="user", content="ping")],
                model=preset.default_model, max_tokens=8,
            )
            typer.echo(f"{preset.id:14} OK   {preset.default_model}")
        except Exception as exc:  # 验活失败不中断其余厂商
            typer.echo(f"{preset.id:14} FAIL {type(exc).__name__}: {exc}")
```

- [x] **Step 6: 运行测试确认通过**

Run: `pytest tests/llm -v`
Expected: 全部 PASS（含 Task 4–6 的用例）

- [x] **Step 7: 提交**

```bash
git add content/llm_providers.yaml src/framework_reader/llm/registry.py \
        src/framework_reader/cli/main.py tests/llm/test_registry.py
git commit -m "feat(llm): 厂商预设与按角色组装，出网只有一条被红线包住的路径"
```

---

### Task 8: 起草器

只产四个非差异化字段。**三个差异化字段哪怕模型主动给了也必须丢弃**——这是 D1 的第一道闸。

**Files:**
- Create: `src/framework_reader/prompts/__init__.py`
- Create: `src/framework_reader/prompts/drafter.md`
- Create: `src/framework_reader/interpret/drafter.py`
- Test: `tests/interpret/test_drafter.py`

**Interfaces:**
- Consumes: Task 1 `Field`/`Basis`/`DRAFTED_FIELDS`、Task 4 `LLMClient`/`Message`
- Produces: `PROMPT_VERSIONS: dict[str, str]`、`DrafterOutputError`、`draft_fields(client, *, control_id, outcome, neighbors, model) -> dict[str, Field]`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_drafter.py`：

```python
import json

import pytest

from framework_reader.interpret.drafter import DrafterOutputError, draft_fields
from framework_reader.interpret.model import Basis, DIFFERENTIATING_FIELDS, DRAFTED_FIELDS
from framework_reader.llm.client import FakeClient

GOOD = json.dumps({
    "intent": "防的是供方在关系存续期变质",
    "plain_zh": "签完合同还得持续盯着供方",
    "practice": {"1": "有台账", "2": "定期复核", "3": "指标化并联动合同"},
    "evidence": "供方复核记录与签字",
}, ensure_ascii=False)


def _draft(response: str):
    return draft_fields(
        FakeClient([response]),
        control_id="NIST-CSF-2.0:GV.SC-07",
        outcome="The risks posed by a supplier ... are monitored",
        neighbors=["NIST-800-53-R5:SR-6"],
        model="claude-opus-5",
    )


def test_produces_exactly_the_four_drafted_fields():
    fields = _draft(GOOD)
    assert set(fields) == set(DRAFTED_FIELDS)


def test_drafted_fields_are_marked_inferred():
    for field in _draft(GOOD).values():
        assert field.basis is Basis.INFERRED


def test_practice_keeps_three_maturity_levels():
    assert set(_draft(GOOD)["practice"].value) == {"1", "2", "3"}


def test_differentiating_fields_are_discarded_even_if_the_model_volunteers_them():
    """D1 的第一道闸：起草器不得为这三个字段供稿。W2 spec §1 D1"""
    payload = json.loads(GOOD)
    payload["common_myth"] = "模型自作主张写的误解"
    payload["auditor_asks"] = ["模型编的追问"]
    fields = _draft(json.dumps(payload, ensure_ascii=False))
    assert set(fields).isdisjoint(DIFFERENTIATING_FIELDS)


def test_control_id_and_outcome_reach_the_prompt():
    client = FakeClient([GOOD])
    draft_fields(
        client,
        control_id="NIST-CSF-2.0:GV.SC-07",
        outcome="供方风险在关系存续期被监控",
        neighbors=["NIST-800-53-R5:SR-6"],
        model="m",
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "GV.SC-07" in sent
    assert "供方风险在关系存续期被监控" in sent
    assert "NIST-800-53-R5:SR-6" in sent


def test_non_json_response_raises_instead_of_guessing():
    with pytest.raises(DrafterOutputError):
        _draft("模型没按格式回，写了一段散文。")


def test_missing_required_field_raises():
    payload = json.loads(GOOD)
    del payload["evidence"]
    with pytest.raises(DrafterOutputError, match="evidence"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_practice_without_three_levels_raises():
    payload = json.loads(GOOD)
    payload["practice"] = {"1": "只有一档"}
    with pytest.raises(DrafterOutputError, match="practice"):
        _draft(json.dumps(payload, ensure_ascii=False))


def test_fenced_json_is_tolerated():
    """模型爱套 ```json 围栏，这个不算格式错误。"""
    fields = _draft(f"```json\n{GOOD}\n```")
    assert set(fields) == set(DRAFTED_FIELDS)
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_drafter.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.drafter`

- [x] **Step 3: 写提示词**

`src/framework_reader/prompts/__init__.py`：

```python
"""提示词与版本号。W2 spec §3.4② —— prompt_version 必须随解读一起留痕。"""
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

PROMPT_VERSIONS = {
    "drafter": "2026.08-d1",
    "questioner": "2026.08-q1",
    "extractor": "2026.08-x1",
}


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
```

`src/framework_reader/prompts/drafter.md`：

```markdown
你在为中文安全团队写国际框架控制的解读初稿。

只输出下面四个字段，**不要输出任何其他字段**：

- `intent`：这条到底在防什么风险。不是翻译条文。
- `plain_zh`：用大白话说它要你干什么。
- `practice`：实践中怎么落地，分成熟度三档，键必须是字符串 "1" / "2" / "3"。
- `evidence`：审计员通常要看什么形态的东西。

硬约束：

1. 只输出一个 JSON 对象，不要解释，不要前后缀文字。
2. 不要写 `common_myth`、`auditor_asks`、`regional_note`——这三个字段由人来写，
   你写了也会被丢弃。
3. 用简体中文。不要音译，不要把「控制」写成「控件」。
4. 不确定的地方写你确定的部分，不要编造具体的法规条号或数字。
```

- [x] **Step 4: 写最小实现**

`src/framework_reader/interpret/drafter.py`：

```python
"""起草器：四个非差异化字段。W2 spec §2 表格第一行

三个差异化字段哪怕模型主动给了也丢弃——D1 的第一道闸。
"""
import json

from framework_reader.interpret.model import Basis, DRAFTED_FIELDS, Field
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import PROMPT_VERSIONS, load_prompt

__all__ = ["DrafterOutputError", "draft_fields", "PROMPT_VERSIONS"]


class DrafterOutputError(Exception):
    """模型输出不符合约定结构。不猜、不修，直接失败。"""


def parse_json_object(text: str) -> dict:
    """容忍 ```json 围栏，其余一律视为格式错误。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise DrafterOutputError(f"不是合法 JSON：{text[:200]}") from exc
    if not isinstance(data, dict):
        raise DrafterOutputError(f"顶层不是对象：{text[:200]}")
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
        f"控制编号：{control_id}\n"
        f"框架原文（公共领域）：{outcome}\n"
        f"官方映射到的 800-53 控制：{', '.join(neighbors) if neighbors else '（无）'}"
    )
    raw = client.complete(
        load_prompt("drafter"), [Message(role="user", content=user)], model=model
    )
    data = parse_json_object(raw)

    for name in DRAFTED_FIELDS:
        if name not in data or data[name] in (None, "", {}, []):
            raise DrafterOutputError(f"缺字段或为空：{name}")

    practice = data["practice"]
    if not isinstance(practice, dict) or set(practice) != {"1", "2", "3"}:
        raise DrafterOutputError(f"practice 必须是三档字典，收到：{practice!r}")

    # 差异化字段一律丢弃，即使模型主动给了。
    return {
        name: Field(value=data[name], basis=Basis.INFERRED) for name in DRAFTED_FIELDS
    }
```

- [x] **Step 5: 运行测试确认通过**

Run: `pytest tests/interpret/test_drafter.py -v`
Expected: 9 passed

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/prompts/ src/framework_reader/interpret/drafter.py \
        tests/interpret/test_drafter.py
git commit -m "feat(interpret): 起草器——只产四个非差异化字段，差异化字段一律丢弃"
```

---

### Task 9: 提问器（Q1/Q2 固定，Q3 自适应）

前两问是常量，**不调模型**——这是把交互期模型调用压到 2 次的关键（W2 spec §2.1）。

**Files:**
- Create: `src/framework_reader/prompts/questioner.md`
- Create: `src/framework_reader/interpret/questioner.py`
- Test: `tests/interpret/test_questioner.py`

**Interfaces:**
- Consumes: Task 1 `Question`/`RawAnswer`、Task 4 `LLMClient`/`Message`、Task 8 `parse_json_object`
- Produces: `Q1_TEXT`、`Q2_TEXT`、`QuestionerOutputError`、`fixed_questions() -> list[Question]`、`adaptive_question(client, *, control_id, outcome, answers, model) -> Question`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_questioner.py`：

```python
import json

import pytest

from framework_reader.interpret.model import RawAnswer
from framework_reader.interpret.questioner import (
    Q1_TEXT,
    Q2_TEXT,
    QuestionerOutputError,
    adaptive_question,
    fixed_questions,
)
from framework_reader.llm.client import FakeClient

ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员一般第二句就问上次复核是谁签的字"),
]


def test_first_two_questions_are_fixed_and_need_no_model_call():
    questions = fixed_questions()
    assert [q.n for q in questions] == [1, 2]
    assert all(q.kind == "fixed" for q in questions)
    assert questions[0].text == Q1_TEXT
    assert questions[1].text == Q2_TEXT


def test_fixed_questions_are_stable_across_calls():
    """106 条问同样的两句——文案漂了，语料就不可比。"""
    assert fixed_questions() == fixed_questions()


def test_adaptive_question_is_number_three():
    client = FakeClient([json.dumps({"question": "欧洲审计员对这条会更严吗？"}, ensure_ascii=False)])
    q = adaptive_question(
        client, control_id="NIST-CSF-2.0:PR.AA-05", outcome="Access permissions…",
        answers=ANSWERS, model="deepseek-chat",
    )
    assert (q.n, q.kind) == (3, "adaptive")
    assert q.text == "欧洲审计员对这条会更严吗？"


def test_adaptive_question_sees_both_previous_answers():
    """第 3 问的全部价值在于模型读过前两答。W2 spec §1.2"""
    client = FakeClient([json.dumps({"question": "追问"}, ensure_ascii=False)])
    adaptive_question(
        client, control_id="X:1", outcome="o", answers=ANSWERS, model="m"
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "权限矩阵表" in sent
    assert "谁签的字" in sent


def test_empty_question_raises():
    client = FakeClient([json.dumps({"question": "   "}, ensure_ascii=False)])
    with pytest.raises(QuestionerOutputError):
        adaptive_question(client, control_id="X:1", outcome="o", answers=ANSWERS, model="m")


def test_non_json_response_raises():
    with pytest.raises(QuestionerOutputError):
        adaptive_question(
            FakeClient(["就问你地域差异吧"]), control_id="X:1", outcome="o",
            answers=ANSWERS, model="m",
        )
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_questioner.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.questioner`

- [x] **Step 3: 写提示词**

`src/framework_reader/prompts/questioner.md`：

```markdown
你在帮一位做过审计的中文安全从业者，把他脑子里的经验挖出来。

他已经回答了两个固定问题：
1. 中文团队对这条控制最常见的误解是什么？
2. 审计员会追问哪几句？

现在你要提**第三个也是最后一个**问题。规则：

1. 默认追问**地域差异**：欧洲与美国审计员对这条的松紧差在哪。
2. 但如果他前两个回答里有某处明显更值得深挖——比如提到了一个具体场景、一次真实
   的争议、一个他一带而过的判断——**改追那一处**，别硬问地域差异。
   很多控制**确实没有**地域差异，硬问会逼他编。
3. 问题必须具体到能一句话回答，不要「能展开讲讲吗」这种空问。
4. 只输出一个 JSON 对象：`{"question": "……"}`。不要解释。
```

- [x] **Step 4: 写最小实现**

`src/framework_reader/interpret/questioner.py`：

```python
"""提问器。W2 spec §2.2

Q1/Q2 是常量，不调模型；只有 Q3 自适应，在作者已说了两轮之后发出。
"""
from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.model import Question, RawAnswer
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

Q1_TEXT = "中文团队对这条控制最常见的误解是什么？"
Q2_TEXT = "审计员会追问哪几句？"


class QuestionerOutputError(Exception):
    """模型没给出可用的问题。"""


def fixed_questions() -> list[Question]:
    return [
        Question(n=1, kind="fixed", text=Q1_TEXT),
        Question(n=2, kind="fixed", text=Q2_TEXT),
    ]


def adaptive_question(
    client: LLMClient,
    *,
    control_id: str,
    outcome: str,
    answers: list[RawAnswer],
    model: str,
) -> Question:
    transcript = "\n".join(f"[答{a.n}] {a.text}" for a in sorted(answers, key=lambda a: a.n))
    user = f"控制编号：{control_id}\n框架原文：{outcome}\n\n他的回答：\n{transcript}"
    try:
        data = parse_json_object(
            client.complete(
                load_prompt("questioner"), [Message(role="user", content=user)], model=model
            )
        )
    except Exception as exc:
        raise QuestionerOutputError(f"提问器输出不可用：{exc}") from exc

    text = str(data.get("question") or "").strip()
    if not text:
        raise QuestionerOutputError("提问器返回了空问题")
    return Question(n=3, kind="adaptive", text=text)
```

- [x] **Step 5: 运行测试确认通过**

Run: `pytest tests/interpret/test_questioner.py -v`
Expected: 6 passed

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/prompts/questioner.md \
        src/framework_reader/interpret/questioner.py tests/interpret/test_questioner.py
git commit -m "feat(interpret): 提问器——前两问固定不调模型，第三问自适应"
```

---

### Task 10: 抽取器（严格）

**只许删、切、重排作者原话，不许引入作者没说过的信息**（W2 spec §2.3）。
输出不合结构时**不写盘、不自动修复**——自动修复等于让模型二次创作（W2 spec §6）。

**Files:**
- Create: `src/framework_reader/prompts/extractor.md`
- Create: `src/framework_reader/interpret/extractor.py`
- Test: `tests/interpret/test_extractor.py`

**Interfaces:**
- Consumes: Task 1 `Field`/`Basis`/`DIFFERENTIATING_FIELDS`/`Question`/`RawAnswer`、Task 4 `LLMClient`、Task 8 `parse_json_object`
- Produces: `ExtractorOutputError`、`FAILURE_DIR: Path`、`extract_fields(client, *, control_id, questions, answers, model, failure_dir=FAILURE_DIR) -> dict[str, Field]`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_extractor.py`：

```python
import json

import pytest

from framework_reader.interpret.extractor import ExtractorOutputError, extract_fields
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import FakeClient

QUESTIONS = [
    Question(n=1, kind="fixed", text="最常见的误解是什么？"),
    Question(n=2, kind="fixed", text="审计员会追问哪几句？"),
    Question(n=3, kind="adaptive", text="欧洲会更严吗？"),
]
ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员会问上次复核是谁签的字，还会问离职当天权限什么时候收的"),
    RawAnswer(n=3, text="欧洲那边会追到具体的复核证据，美国更看流程写没写"),
]

GOOD = json.dumps({
    "common_myth": "他们以为有张权限矩阵表就算做到了",
    "auditor_asks": ["上次复核是谁签的字", "离职当天权限什么时候收的"],
    "regional_note": "欧洲那边会追到具体的复核证据，美国更看流程写没写",
}, ensure_ascii=False)


def _extract(response: str, tmp_path=None):
    return extract_fields(
        FakeClient([response]),
        control_id="NIST-CSF-2.0:PR.AA-05",
        questions=QUESTIONS,
        answers=ANSWERS,
        model="deepseek-chat",
        failure_dir=tmp_path,
    )


def test_produces_exactly_the_three_differentiating_fields(tmp_path):
    assert set(_extract(GOOD, tmp_path)) == set(DIFFERENTIATING_FIELDS)


def test_fields_are_marked_practitioner_sourced(tmp_path):
    """这三个字段的依据是作者的从业经验，不是原文也不是推断。W2 spec §2.4"""
    for field in _extract(GOOD, tmp_path).values():
        assert field.basis is Basis.PRACTITIONER


def test_null_field_is_allowed(tmp_path):
    """留空是信号。作者没料的字段不许模型补。W2 spec §2.2、§2.3"""
    payload = json.loads(GOOD)
    payload["regional_note"] = None
    fields = _extract(json.dumps(payload, ensure_ascii=False), tmp_path)
    assert fields["regional_note"].value is None


def test_all_raw_answers_and_questions_reach_the_prompt(tmp_path):
    client = FakeClient([GOOD])
    extract_fields(
        client, control_id="X:1", questions=QUESTIONS, answers=ANSWERS,
        model="m", failure_dir=tmp_path,
    )
    sent = client.calls[0]["messages"][0]["content"]
    for answer in ANSWERS:
        assert answer.text in sent
    for question in QUESTIONS:
        assert question.text in sent


def test_wrong_type_raises_instead_of_being_coerced(tmp_path):
    """auditor_asks 必须是列表。把字符串强转成 [字符串] 属于替模型收拾——不做。"""
    payload = json.loads(GOOD)
    payload["auditor_asks"] = "上次复核是谁签的字"
    with pytest.raises(ExtractorOutputError, match="auditor_asks"):
        _extract(json.dumps(payload, ensure_ascii=False), tmp_path)


def test_missing_key_raises(tmp_path):
    payload = json.loads(GOOD)
    del payload["common_myth"]
    with pytest.raises(ExtractorOutputError, match="common_myth"):
        _extract(json.dumps(payload, ensure_ascii=False), tmp_path)


def test_failure_dumps_the_raw_response_for_diagnosis(tmp_path):
    with pytest.raises(ExtractorOutputError):
        _extract("模型写了一段散文", tmp_path)
    dumps = list(tmp_path.glob("*.txt"))
    assert len(dumps) == 1
    assert "模型写了一段散文" in dumps[0].read_text(encoding="utf-8")


def test_failure_writes_nothing_but_the_dump(tmp_path):
    """抽取失败不得污染 content/——只留诊断文件。W2 spec §6"""
    with pytest.raises(ExtractorOutputError):
        _extract("坏输出", tmp_path)
    assert all(p.suffix == ".txt" for p in tmp_path.rglob("*") if p.is_file())
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_extractor.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.extractor`

- [x] **Step 3: 写提示词**

`src/framework_reader/prompts/extractor.md`：

```markdown
你在把一位安全从业者的口语化回答，整理成三个结构化字段。

**你不是作者。你是抄写员。**

绝对规则：

1. **只许删、切、重排他的原话。**
2. **不许引入他没说过的信息。** 不补充、不举例、不推广到他没提的场景。
3. **不许润色措辞。** 他说「审计员一般第二句就问你上次复核是谁签的字」，
   你就保留这个说法，不要改成「审计人员通常关注权限复核的执行情况及记录留存」。
   前者是他，后者是任何模型都写得出的话——那正是这份工作要避免的。
4. **他没答到的字段就填 `null`。** 留空是信号，不是缺陷。绝不允许你替他补。

三个字段：

- `common_myth`：字符串或 null。中文团队对这条的常见误解。
- `auditor_asks`：字符串数组或 null。审计员会追问的话，一句一条，尽量保留原话口吻。
- `regional_note`：字符串或 null。欧洲/美国审计员对这条的松紧差异。

只输出一个 JSON 对象，三个键齐全（值可以是 null），不要解释，不要前后缀文字。
```

- [x] **Step 4: 写最小实现**

`src/framework_reader/interpret/extractor.py`：

```python
"""抽取器：严格抽取三个差异化字段。W2 spec §2.3

只许删、切、重排；输出不合结构时不写盘、不自动修复。
"""
from pathlib import Path

from framework_reader.interpret.drafter import parse_json_object
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Field,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import LLMClient, Message
from framework_reader.prompts import load_prompt

FAILURE_DIR = Path("build/extract_failures")


class ExtractorOutputError(Exception):
    """抽取器输出不合结构。不修、不写盘。"""


def _dump_failure(failure_dir: Path | None, control_id: str, raw: str) -> None:
    if failure_dir is None:
        return
    failure_dir.mkdir(parents=True, exist_ok=True)
    name = control_id.replace(":", "_").replace("/", "_")
    (failure_dir / f"{name}.txt").write_text(raw, encoding="utf-8")


def extract_fields(
    client: LLMClient,
    *,
    control_id: str,
    questions: list[Question],
    answers: list[RawAnswer],
    model: str,
    failure_dir: Path | None = FAILURE_DIR,
) -> dict[str, Field]:
    by_n = {q.n: q.text for q in questions}
    transcript = "\n\n".join(
        f"问{a.n}：{by_n.get(a.n, '')}\n答{a.n}：{a.text}"
        for a in sorted(answers, key=lambda a: a.n)
    )
    user = f"控制编号：{control_id}\n\n{transcript}"

    raw = client.complete(
        load_prompt("extractor"), [Message(role="user", content=user)], model=model
    )
    try:
        data = parse_json_object(raw)
    except Exception as exc:
        _dump_failure(failure_dir, control_id, raw)
        raise ExtractorOutputError(f"抽取器输出不是合法 JSON：{raw[:200]}") from exc

    for name in DIFFERENTIATING_FIELDS:
        if name not in data:
            _dump_failure(failure_dir, control_id, raw)
            raise ExtractorOutputError(f"缺键：{name}")

    if data["auditor_asks"] is not None and not isinstance(data["auditor_asks"], list):
        _dump_failure(failure_dir, control_id, raw)
        raise ExtractorOutputError(
            f"auditor_asks 必须是数组或 null，收到 {type(data['auditor_asks']).__name__}"
        )
    for name in ("common_myth", "regional_note"):
        if data[name] is not None and not isinstance(data[name], str):
            _dump_failure(failure_dir, control_id, raw)
            raise ExtractorOutputError(
                f"{name} 必须是字符串或 null，收到 {type(data[name]).__name__}"
            )

    return {
        name: Field(value=data[name], basis=Basis.PRACTITIONER)
        for name in DIFFERENTIATING_FIELDS
    }
```

- [x] **Step 5: 运行测试确认通过**

Run: `pytest tests/interpret/test_extractor.py -v`
Expected: 8 passed

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/prompts/extractor.md \
        src/framework_reader/interpret/extractor.py tests/interpret/test_extractor.py
git commit -m "feat(interpret): 严格抽取器——只删切重排，失败不修不写盘"
```

---

### Task 11: 抽取忠实度 lint

字段值与作者原话的字符二元组重合度。**这是近似检查**：拦得住整段编造，拦不住换近义词。
真正的防线是 `$EDITOR` 里逐条签字（Task 13）。**不做成构建断言**，误报率会毁掉手感。

阈值**不预设常数**，在 Task 14 用 3 条黄金样例标定。

**Files:**
- Create: `src/framework_reader/interpret/lint.py`
- Test: `tests/interpret/test_lint.py`

**Interfaces:**
- Consumes: Task 1 `Field`/`RawAnswer`/`DIFFERENTIATING_FIELDS`
- Produces: `bigram_overlap(text, source) -> float`、`field_scores(fields, answers) -> dict[str, float]`、`flag_low_fidelity(scores, threshold) -> list[str]`、`suggest_threshold(scores, margin=0.05) -> float`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_lint.py`：

```python
import pytest

from framework_reader.interpret.lint import (
    bigram_overlap,
    field_scores,
    flag_low_fidelity,
    suggest_threshold,
)
from framework_reader.interpret.model import Basis, Field, RawAnswer

ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员会问上次复核是谁签的字"),
]


def test_verbatim_extraction_scores_one():
    assert bigram_overlap("他们以为有张权限矩阵表", "他们以为有张权限矩阵表就算做到了") == 1.0


def test_wholesale_invention_scores_near_zero():
    score = bigram_overlap(
        "权限管理体系应当健全并定期开展合规性评估", "他们以为有张权限矩阵表就算做到了"
    )
    assert score < 0.2


def test_paraphrase_scores_in_between_and_is_not_caught():
    """诚实标注这条检查的能力边界：换近义词它拦不住。W2 spec §2.3"""
    score = bigram_overlap("他们觉得有个权限矩阵表就行了", "他们以为有张权限矩阵表就算做到了")
    assert 0.2 < score < 1.0


def test_empty_field_scores_one_not_zero():
    """留空是信号，不该被 lint 当成不忠实。"""
    assert bigram_overlap("", "任意原话") == 1.0


def test_list_field_is_scored_against_all_answers_joined():
    scores = field_scores(
        {
            "common_myth": Field(value="他们以为有张权限矩阵表", basis=Basis.PRACTITIONER),
            "auditor_asks": Field(value=["上次复核是谁签的字"], basis=Basis.PRACTITIONER),
            "regional_note": Field(value=None, basis=Basis.PRACTITIONER),
        },
        ANSWERS,
    )
    assert scores["common_myth"] == pytest.approx(1.0)
    assert scores["auditor_asks"] == pytest.approx(1.0)
    assert scores["regional_note"] == pytest.approx(1.0)


def test_flag_low_fidelity_returns_field_names_below_threshold():
    assert flag_low_fidelity({"common_myth": 0.2, "auditor_asks": 0.9}, 0.5) == ["common_myth"]


def test_suggest_threshold_sits_below_the_worst_faithful_sample():
    """标定法：取人工判定为忠实的最低重合度再下调一档。W2 spec §2.3"""
    assert suggest_threshold([0.9, 0.75, 0.82], margin=0.05) == pytest.approx(0.70)


def test_suggest_threshold_never_goes_negative():
    assert suggest_threshold([0.01], margin=0.05) == 0.0
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_lint.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.lint`

- [x] **Step 3: 写最小实现**

`src/framework_reader/interpret/lint.py`：

```python
"""抽取忠实度的近似检查。W2 spec §2.3

拦得住整段编造，拦不住换近义词。真正的防线是 $EDITOR 里逐条签字。
不做成构建断言——误报率会毁掉手感。
"""
from framework_reader.interpret.model import DIFFERENTIATING_FIELDS, Field, RawAnswer


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
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/interpret/test_lint.py -v`
Expected: 8 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/interpret/lint.py tests/interpret/test_lint.py
git commit -m "feat(interpret): 抽取忠实度 lint，阈值待黄金样例标定"
```

---

### Task 12: `fr draft --all` 批量起草

起草与访谈分离是吞吐的关键：访谈时不能有一分钟花在等起草上（W2 spec §2.1）。

**Files:**
- Modify: `src/framework_reader/query/api.py`
- Create: `src/framework_reader/interpret/batch.py`
- Modify: `src/framework_reader/cli/main.py`
- Test: `tests/query/test_list_controls.py`
- Test: `tests/interpret/test_batch.py`

**Interfaces:**
- Consumes: Task 1/2/8、W1 的 `QueryAPI`
- Produces:
  - `QueryAPI.list_controls(framework_id: str, *, active_only: bool = True, leaf_only: bool = False) -> list[ControlView]`
  - `batch.draft_all(store, api, client, *, framework_id, model, prompt_version, provider, jobs=4, force=False) -> list[str]`（返回新写入的 control_id）

- [x] **Step 1: 写失败测试**

`tests/query/test_list_controls.py`：

```python
import sqlite3

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import (
    ControlStatus,
    Framework,
    FrameworkControl,
    LicenseTier,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM", framework_id="NIST-CSF-2.0",
                         label="Continuous Monitoring", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.DP-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Withdrawn one", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE,
                         status=ControlStatus.DEPRECATED),
    ])
    conn.close()
    return path


def test_active_only_excludes_deprecated(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0")]
    assert "NIST-CSF-2.0:DE.DP-01" not in ids


def test_leaf_only_excludes_categories(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0", leaf_only=True)]
    assert ids == ["NIST-CSF-2.0:DE.CM-01"]


def test_results_are_sorted_by_id(db):
    ids = [c.id for c in QueryAPI(db).list_controls("NIST-CSF-2.0")]
    assert ids == sorted(ids)
```

`tests/interpret/test_batch.py`：

```python
import json
import sqlite3

import pytest

from framework_reader.interpret.batch import draft_all
from framework_reader.interpret.model import Basis, DRAFTED_FIELDS, InterpretationState
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import FakeClient
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

DRAFT_JSON = json.dumps({
    "intent": "意图", "plain_zh": "大白话",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "证据",
}, ensure_ascii=False)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM", framework_id="NIST-CSF-2.0",
                         label="Continuous Monitoring", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         parent_id="NIST-CSF-2.0:DE.CM",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    conn.close()
    return path


def test_writes_one_draft_file_per_leaf_control(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    written = draft_all(
        store, QueryAPI(db), FakeClient([DRAFT_JSON]),
        framework_id="NIST-CSF-2.0", model="m",
        prompt_version="2026.08-d1", provider="anthropic", jobs=1,
    )
    assert written == ["NIST-CSF-2.0:DE.CM-01"]
    interp = store.load("NIST-CSF-2.0:DE.CM-01")
    assert interp.state is InterpretationState.DRAFT
    assert set(DRAFTED_FIELDS) <= set(interp.fields)


def test_differentiating_fields_start_empty_and_practitioner_sourced(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="2026.08-d1", provider="anthropic", jobs=1)
    interp = store.load("NIST-CSF-2.0:DE.CM-01")
    for name in ("common_myth", "auditor_asks", "regional_note"):
        assert interp.fields[name].value is None
        assert interp.fields[name].basis is Basis.PRACTITIONER


def test_records_drafter_provenance(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="claude-opus-5",
              prompt_version="2026.08-d1", provider="anthropic", jobs=1)
    ref = store.load("NIST-CSF-2.0:DE.CM-01").provenance.drafter
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "anthropic", "claude-opus-5", "2026.08-d1"
    )


def test_existing_files_are_skipped_without_force(tmp_path, db):
    store = InterpretationStore(tmp_path / "interp")
    draft_all(store, QueryAPI(db), FakeClient([DRAFT_JSON]),
              framework_id="NIST-CSF-2.0", model="m",
              prompt_version="v", provider="p", jobs=1)
    again = draft_all(store, QueryAPI(db), FakeClient([]),
                      framework_id="NIST-CSF-2.0", model="m",
                      prompt_version="v", provider="p", jobs=1)
    assert again == [], "已有文件不得被重跑覆盖——作者的访谈内容会丢"
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/query/test_list_controls.py tests/interpret/test_batch.py -v`
Expected: FAIL —— `AttributeError: 'QueryAPI' object has no attribute 'list_controls'`

- [x] **Step 3: 扩 QueryAPI**

在 `src/framework_reader/query/api.py` 的 `search` 方法之前插入：

```python
    def list_controls(
        self, framework_id: str, *, active_only: bool = True, leaf_only: bool = False
    ) -> list[ControlView]:
        clauses = ["framework_id = ?"]
        params: list[object] = [framework_id]
        if active_only:
            clauses.append("status <> 'deprecated'")
        if leaf_only:
            clauses.append("id NOT IN (SELECT parent_id FROM framework_control "
                           "WHERE parent_id IS NOT NULL)")
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM framework_control "
            f"WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]
```

- [x] **Step 4: 写批量起草**

`src/framework_reader/interpret/batch.py`：

```python
"""批量起草。W2 spec §2.1：起草与访谈分离，访谈期不等模型。"""
from concurrent.futures import ThreadPoolExecutor

from framework_reader.interpret.drafter import draft_fields
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Field,
    Interpretation,
    InterpretationProvenance,
    ModelRef,
)
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import LLMClient
from framework_reader.query.api import QueryAPI


def _empty_differentiating() -> dict[str, Field]:
    return {n: Field(value=None, basis=Basis.PRACTITIONER) for n in DIFFERENTIATING_FIELDS}


def draft_all(
    store: InterpretationStore,
    api: QueryAPI,
    client: LLMClient,
    *,
    framework_id: str,
    model: str,
    prompt_version: str,
    provider: str,
    jobs: int = 4,
    force: bool = False,
) -> list[str]:
    targets = [
        c for c in api.list_controls(framework_id, active_only=True, leaf_only=True)
        if force or not store.exists(c.id)
    ]

    def one(control) -> str:
        neighbors = [
            n.control_id for n in api.neighbors(control.id, exportable_only=True)
            if n.control_id.startswith("NIST-800-53-R5:")
        ]
        fields = draft_fields(
            client, control_id=control.id, outcome=control.label,
            neighbors=neighbors, model=model,
        )
        fields.update(_empty_differentiating())
        store.save(Interpretation(
            control_id=control.id,
            fields=fields,
            provenance=InterpretationProvenance(
                drafter=ModelRef(
                    provider=provider, model=model, prompt_version=prompt_version
                )
            ),
        ))
        return control.id

    if jobs <= 1:
        return [one(c) for c in targets]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return sorted(pool.map(one, targets))
```

- [x] **Step 5: 挂 CLI**

`src/framework_reader/cli/main.py` 追加：

```python
@app.command("draft")
def draft(
    framework_id: str = "NIST-CSF-2.0",
    jobs: int = 4,
    force: bool = False,
    db: Path = DEFAULT_DB,
) -> None:
    """批量起草四个非差异化字段。离线跑，可并发。"""
    import sqlite3

    from framework_reader.interpret.batch import draft_all
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
    from framework_reader.prompts import PROMPT_VERSIONS

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()

    written = draft_all(
        InterpretationStore(), QueryAPI(db),
        registry.build("drafter", guard=guard),
        framework_id=framework_id, model=role.model,
        prompt_version=PROMPT_VERSIONS["drafter"], provider=role.provider,
        jobs=jobs, force=force,
    )
    typer.echo(f"起草 {len(written)} 条")
```

- [x] **Step 6: 运行测试确认通过**

Run: `pytest tests/query/test_list_controls.py tests/interpret/test_batch.py -v`
Expected: 7 passed

- [x] **Step 7: 提交**

```bash
git add src/framework_reader/query/api.py src/framework_reader/interpret/batch.py \
        src/framework_reader/cli/main.py tests/query/test_list_controls.py \
        tests/interpret/test_batch.py
git commit -m "feat(interpret): 批量起草，起草与访谈分离"
```

---

### Task 13: 访谈会话

把访谈逻辑与终端 IO 分开：`InterviewSession` 是可测的纯逻辑，CLI 壳只负责读键盘。
**答完一问立刻落盘**，Ctrl-C / 断网 / 模型挂都不丢话（W2 spec §6）。

**Files:**
- Create: `src/framework_reader/interpret/session.py`
- Test: `tests/interpret/test_session.py`

**Interfaces:**
- Consumes: Task 1/2/9/10
- Produces: `InterviewSession(store, questioner_client, extractor_client, *, outcome_lookup, questioner_model, extractor_model, extractor_provider, extractor_prompt_version)`，方法 `next_question(control_id) -> Question | None`、`record(control_id, n, text) -> None`、`finish(control_id) -> Interpretation`
  - `outcome_lookup: Callable[[str], str]` —— 取该控制的框架原文（CSF 公共领域 outcome）。**不能拿 `intent` 初稿顶替**：提问器的提示词写的是「框架原文」，喂初稿等于让模型顺着自己的话往下问。

- [x] **Step 1: 写失败测试**

`tests/interpret/test_session.py`：

```python
import json

import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
)
from framework_reader.interpret.questioner import Q1_TEXT, Q2_TEXT
from framework_reader.interpret.session import InterviewSession
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import FakeClient

CID = "NIST-CSF-2.0:PR.AA-05"
ADAPTIVE = json.dumps({"question": "欧洲会更严吗？"}, ensure_ascii=False)
EXTRACTED = json.dumps({
    "common_myth": "以为有张权限表就行",
    "auditor_asks": ["上次复核谁签的字"],
    "regional_note": None,
}, ensure_ascii=False)


def _draft_file(store: InterpretationStore) -> None:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    store.save(Interpretation(control_id=CID, fields=fields))


def _session(tmp_path, questioner=None, extractor=None) -> tuple:
    store = InterpretationStore(tmp_path)
    _draft_file(store)
    session = InterviewSession(
        store,
        questioner or FakeClient([ADAPTIVE]),
        extractor or FakeClient([EXTRACTED]),
        outcome_lookup=lambda cid: "Access permissions are defined and reviewed",
        questioner_model="q", extractor_model="x",
        extractor_provider="deepseek", extractor_prompt_version="2026.08-x1",
    )
    return store, session


def test_first_question_is_fixed_q1(tmp_path):
    _, session = _session(tmp_path)
    assert session.next_question(CID).text == Q1_TEXT


def test_second_question_is_fixed_q2_and_costs_no_model_call(tmp_path):
    questioner = FakeClient([])          # 空队列：调一次就炸
    store, session = _session(tmp_path, questioner=questioner)
    session.record(CID, 1, "以为有张权限表就行")
    assert session.next_question(CID).text == Q2_TEXT
    assert questioner.calls == []


def test_third_question_is_adaptive(tmp_path):
    _, session = _session(tmp_path)
    session.record(CID, 1, "以为有张权限表就行")
    session.record(CID, 2, "上次复核谁签的字")
    q = session.next_question(CID)
    assert (q.n, q.kind, q.text) == (3, "adaptive", "欧洲会更严吗？")


def test_adaptive_question_gets_the_framework_text_not_the_draft(tmp_path):
    """提问器要看框架原文；喂 intent 初稿等于让模型顺着自己的话往下问。"""
    questioner = FakeClient([ADAPTIVE])
    _, session = _session(tmp_path, questioner=questioner)
    session.record(CID, 1, "答一")
    session.record(CID, 2, "答二")
    session.next_question(CID)
    sent = questioner.calls[0]["messages"][0]["content"]
    assert "Access permissions are defined and reviewed" in sent
    assert "草稿" not in sent


def test_no_more_questions_after_three_answers(tmp_path):
    _, session = _session(tmp_path)
    for n, text in ((1, "a"), (2, "b"), (3, "c")):
        session.record(CID, n, text)
    assert session.next_question(CID) is None


def test_answer_is_persisted_immediately(tmp_path):
    """答完一问就落盘——这是 W2 spec §6 的主线。"""
    store, session = _session(tmp_path)
    session.record(CID, 1, "以为有张权限表就行")
    assert [r.text for r in store.load(CID).interview.raw] == ["以为有张权限表就行"]


def test_resume_picks_up_where_it_stopped(tmp_path):
    """崩了重进，从第 2 问继续，不重问第 1 问。"""
    store, session = _session(tmp_path)
    session.record(CID, 1, "第一答")
    fresh = InterviewSession(
        store, FakeClient([ADAPTIVE]), FakeClient([EXTRACTED]),
        outcome_lookup=lambda cid: "Access permissions are defined and reviewed",
        questioner_model="q", extractor_model="x",
        extractor_provider="deepseek", extractor_prompt_version="v",
    )
    assert fresh.next_question(CID).text == Q2_TEXT


def test_finish_runs_extraction_and_moves_to_interviewed(tmp_path):
    store, session = _session(tmp_path)
    for n, text in ((1, "以为有张权限表就行"), (2, "上次复核谁签的字"), (3, "没差别")):
        session.record(CID, n, text)
    session.next_question(CID)
    result = session.finish(CID)
    assert result.state is InterpretationState.INTERVIEWED
    assert result.fields["common_myth"].value == "以为有张权限表就行"
    assert result.fields["common_myth"].basis is Basis.PRACTITIONER


def test_finish_records_extractor_provenance(tmp_path):
    store, session = _session(tmp_path)
    for n in (1, 2, 3):
        session.record(CID, n, f"答{n}")
    session.next_question(CID)
    ref = session.finish(CID).provenance.extractor
    assert (ref.provider, ref.model, ref.prompt_version) == (
        "deepseek", "x", "2026.08-x1"
    )


def test_finish_before_three_answers_is_refused(tmp_path):
    _, session = _session(tmp_path)
    session.record(CID, 1, "只答了一问")
    with pytest.raises(ValueError, match="三问"):
        session.finish(CID)


def test_drafted_fields_survive_the_interview(tmp_path):
    store, session = _session(tmp_path)
    for n in (1, 2, 3):
        session.record(CID, n, f"答{n}")
    session.next_question(CID)
    result = session.finish(CID)
    assert result.fields["intent"].value == "草稿"
    assert result.fields["intent"].basis is Basis.INFERRED
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_session.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.session`

- [x] **Step 3: 写最小实现**

`src/framework_reader/interpret/session.py`：

```python
"""访谈会话：可测的纯逻辑，与终端 IO 分离。W2 spec §5、§6"""
from collections.abc import Callable

from framework_reader.interpret.extractor import extract_fields
from framework_reader.interpret.model import (
    Interpretation,
    InterpretationState,
    ModelRef,
    Question,
)
from framework_reader.interpret.questioner import adaptive_question, fixed_questions
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import LLMClient

TOTAL_QUESTIONS = 3


class InterviewSession:
    def __init__(
        self,
        store: InterpretationStore,
        questioner_client: LLMClient,
        extractor_client: LLMClient,
        *,
        outcome_lookup: Callable[[str], str],
        questioner_model: str,
        extractor_model: str,
        extractor_provider: str,
        extractor_prompt_version: str,
    ) -> None:
        self._store = store
        self._questioner = questioner_client
        self._extractor = extractor_client
        self._outcome_lookup = outcome_lookup
        self._questioner_model = questioner_model
        self._extractor_model = extractor_model
        self._extractor_provider = extractor_provider
        self._extractor_prompt_version = extractor_prompt_version

    def next_question(self, control_id: str) -> Question | None:
        interp = self._store.load(control_id)
        answered = {r.n for r in interp.interview.raw}
        fixed = fixed_questions()

        for question in fixed:
            if question.n not in answered:
                self._remember(interp, question)
                return question

        if TOTAL_QUESTIONS in answered:
            return None

        question = adaptive_question(
            self._questioner,
            control_id=control_id,
            outcome=self._outcome_lookup(control_id),
            answers=interp.interview.raw,
            model=self._questioner_model,
        )
        self._remember(interp, question)
        return question

    def _remember(self, interp: Interpretation, question: Question) -> None:
        kept = [q for q in interp.interview.questions if q.n != question.n]
        kept.append(question)
        interp.interview.questions = sorted(kept, key=lambda q: q.n)
        self._store.save(interp)

    def record(self, control_id: str, n: int, text: str) -> None:
        """答完一问立刻落盘。W2 spec §6"""
        self._store.append_raw(control_id, n, text)

    def finish(self, control_id: str) -> Interpretation:
        interp = self._store.load(control_id)
        if len(interp.interview.raw) < TOTAL_QUESTIONS:
            raise ValueError(
                f"{control_id} 还没答满三问（当前 {len(interp.interview.raw)} 条）"
            )
        fields = extract_fields(
            self._extractor,
            control_id=control_id,
            questions=interp.interview.questions,
            answers=interp.interview.raw,
            model=self._extractor_model,
        )
        interp.fields.update(fields)
        interp.state = InterpretationState.INTERVIEWED
        interp.provenance.extractor = ModelRef(
            provider=self._extractor_provider,
            model=self._extractor_model,
            prompt_version=self._extractor_prompt_version,
        )
        self._store.save(interp)
        return interp
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/interpret/test_session.py -v`
Expected: 10 passed

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/interpret/session.py tests/interpret/test_session.py
git commit -m "feat(interpret): 访谈会话——逻辑与终端分离，答完一问即落盘"
```

---

### Task 14: 访谈终端壳与 `$EDITOR` 签字

`$EDITOR` 里字段值与原话并列，作者敲键签字才生效——**这是抽取忠实度的真正防线**
（lint 只是近似检查）。签完的 YAML 就是最终 ship 的文件，中间没有一层 TUI 状态在翻译。

**Files:**
- Create: `src/framework_reader/cli/interview.py`
- Modify: `src/framework_reader/cli/main.py`
- Modify: `pyproject.toml`（加 `prompt_toolkit`、`anthropic`、`httpx`）
- Test: `tests/cli/test_interview_render.py`

**Interfaces:**
- Consumes: Task 1/2/11/13
- Produces: `render_header(interp, control_label, index, total) -> str`、`annotated_yaml(interp, scores, threshold) -> str`、`sign(interp, signer, now) -> Interpretation`、`run_editor(path, editor_cmd, runner=subprocess.run) -> None`、CLI `fr interview`

- [x] **Step 1: 写失败测试**

`tests/cli/test_interview_render.py`：

```python
from datetime import datetime, timezone

import pytest
import yaml
from pydantic import ValidationError

from framework_reader.cli.interview import annotated_yaml, render_header, run_editor, sign
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationState,
    RawAnswer,
)


def _interp() -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value="以为有张权限表就行", basis=Basis.PRACTITIONER)
    interp = Interpretation(control_id="NIST-CSF-2.0:PR.AA-05", fields=fields)
    interp.interview.raw = [RawAnswer(n=1, text="他们以为有张权限表就行，其实差远了")]
    return interp


def test_header_shows_position_and_control():
    header = render_header(_interp(), "Access permissions are defined", 3, 106)
    assert "PR.AA-05" in header
    assert "3/106" in header
    assert "Access permissions are defined" in header


def test_annotated_yaml_puts_the_authors_own_words_above_the_field():
    text = annotated_yaml(_interp(), scores={}, threshold=0.5)
    lines = text.splitlines()
    idx = next(i for i, l in enumerate(lines) if l.strip().startswith("common_myth:"))
    assert any("他们以为有张权限表就行，其实差远了" in l for l in lines[max(0, idx - 4):idx])


def test_annotated_yaml_flags_fields_below_threshold():
    text = annotated_yaml(_interp(), scores={"common_myth": 0.11}, threshold=0.5)
    assert "抽取忠实度偏低" in text
    assert "0.11" in text


def test_annotated_yaml_is_still_parseable_yaml():
    """注释不能把文件写坏——签完字它就是要 ship 的那个文件。"""
    text = annotated_yaml(_interp(), scores={"common_myth": 0.11}, threshold=0.5)
    reloaded = Interpretation(**yaml.safe_load(text))
    assert reloaded.control_id == "NIST-CSF-2.0:PR.AA-05"
    assert reloaded.fields["common_myth"].value == "以为有张权限表就行"


def test_sign_moves_to_confirmed_with_who_and_when():
    now = datetime(2026, 8, 25, 14, 3, tzinfo=timezone.utc)
    signed = sign(_interp(), "jc", now)
    assert signed.state is InterpretationState.CONFIRMED
    assert signed.provenance.confirmed_by == "jc"
    assert signed.provenance.confirmed_at == now


def test_ai_cannot_sign():
    with pytest.raises(ValidationError):
        sign(_interp(), "ai:deepseek-chat", datetime.now(timezone.utc))


def test_run_editor_invokes_the_configured_command(tmp_path):
    calls: list = []
    path = tmp_path / "x.yaml"
    path.write_text("k: v", encoding="utf-8")
    run_editor(path, "vim", runner=lambda argv, check: calls.append((argv, check)))
    assert calls == [(["vim", str(path)], True)]
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/cli/test_interview_render.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.cli.interview`

- [x] **Step 3: 加依赖**

`pyproject.toml` 的 `dependencies` 改为：

```toml
dependencies = [
    "pydantic>=2.7",
    "PyYAML>=6.0",
    "openpyxl>=3.1",
    "typer>=0.12",
    "prompt_toolkit>=3.0",
    "httpx>=0.27",
    "anthropic>=0.40",
]
```

- [x] **Step 4: 写最小实现**

`src/framework_reader/cli/interview.py`：

```python
"""访谈终端壳与 $EDITOR 签字。W2 spec §5

逻辑在 interpret/session.py；本文件只负责渲染与读键盘。
"""
import os
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

from framework_reader.interpret.model import (
    DIFFERENTIATING_FIELDS,
    Interpretation,
    InterpretationState,
)

Runner = Callable[[list[str], bool], object]


def render_header(
    interp: Interpretation, control_label: str, index: int, total: int
) -> str:
    local = interp.control_id.split(":", 1)[-1]
    intent = interp.fields["intent"].value or ""
    return (
        f"┌ {local} · {index}/{total} " + "─" * 40 + "\n"
        f"│ {control_label}\n"
        f"│\n"
        f"│ 初稿 intent  {intent}\n"
        f"│              ^D 展开全部 4 条初稿   ^S 存盘退出   ^K 跳过本条\n"
        + "└" + "─" * 62
    )


def annotated_yaml(
    interp: Interpretation, scores: dict[str, float], threshold: float
) -> str:
    """把原话与 lint 结果以注释贴在差异化字段上方。注释不影响 YAML 解析。"""
    payload = interp.model_dump(mode="json", exclude_none=False)
    text = yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    raw_all = " / ".join(r.text for r in interp.interview.raw)

    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        for name in DIFFERENTIATING_FIELDS:
            if stripped.startswith(f"{name}:"):
                indent = " " * (len(line) - len(line.lstrip()))
                out.append(f"{indent}# 你说：{raw_all}")
                score = scores.get(name)
                if score is not None and score < threshold:
                    out.append(
                        f"{indent}# ⚠ 抽取忠实度偏低（{score:.2f} < {threshold:.2f}）"
                        f"——这段是不是模型自己写的？"
                    )
                break
        out.append(line)
    return "\n".join(out) + "\n"


def sign(interp: Interpretation, signer: str, now: datetime) -> Interpretation:
    data = interp.model_dump()
    data["state"] = InterpretationState.CONFIRMED
    data["provenance"]["confirmed_by"] = signer
    data["provenance"]["confirmed_at"] = now
    return Interpretation(**data)


def run_editor(path: Path, editor_cmd: str, runner: Runner = subprocess.run) -> None:
    runner([editor_cmd, str(path)], True)


def default_editor() -> str:
    return os.environ.get("EDITOR") or "vi"
```

- [x] **Step 5: 挂 CLI**

`src/framework_reader/cli/main.py` 追加：

```python
@app.command("interview")
def interview(
    control_id: str = typer.Argument(None),
    next_: bool = typer.Option(False, "--next", help="自动取下一条 draft"),
    signer: str = "jc",
    db: Path = DEFAULT_DB,
) -> None:
    """访谈一条控制：三问三答，抽取，$EDITOR 签字。"""
    import sqlite3
    from datetime import datetime, timezone

    import yaml
    from prompt_toolkit import prompt as ptk_prompt

    from framework_reader.cli.interview import (
        annotated_yaml, default_editor, render_header, run_editor, sign,
    )
    from framework_reader.interpret.lint import field_scores
    from framework_reader.interpret.model import Interpretation, InterpretationState
    from framework_reader.interpret.session import InterviewSession
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
    from framework_reader.prompts import PROMPT_VERSIONS

    store = InterpretationStore()
    if next_ or control_id is None:
        drafts = store.by_state(InterpretationState.DRAFT)
        if not drafts:
            typer.echo("没有待访谈的 draft")
            raise typer.Exit(0)
        control_id = drafts[0].control_id

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()

    api = QueryAPI(db)
    session = InterviewSession(
        store,
        registry.build("questioner", guard=guard),
        registry.build("extractor", guard=guard),
        outcome_lookup=lambda cid: (api.get_control(cid).label if api.get_control(cid) else ""),
        questioner_model=registry.role("questioner").model,
        extractor_model=registry.role("extractor").model,
        extractor_provider=registry.role("extractor").provider,
        extractor_prompt_version=PROMPT_VERSIONS["extractor"],
    )

    control = api.get_control(control_id)
    leaves = [c.id for c in api.list_controls(control_id.split(":", 1)[0], leaf_only=True)]
    index = leaves.index(control_id) + 1 if control_id in leaves else 0
    typer.echo(
        render_header(store.load(control_id), control.label if control else "",
                      index, len(leaves))
    )

    try:
        while (question := session.next_question(control_id)) is not None:
            answer = ptk_prompt(f" [{question.n}/3] {question.text}\n ▸ ", multiline=False)
            session.record(control_id, question.n, answer)
    except KeyboardInterrupt:
        typer.echo("\n已答部分已落盘，`fr interview --resume` 可继续")
        raise typer.Exit(130)

    interp = session.finish(control_id)
    path = store.path_for(control_id)
    scores = field_scores(interp.fields, interp.interview.raw)
    # Task 15 会把这个 0.5 换成 content/lint.yaml 里标定出来的阈值。
    path.write_text(annotated_yaml(interp, scores, threshold=0.5), encoding="utf-8")
    run_editor(path, default_editor())

    edited = Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))
    store.save(sign(edited, signer, datetime.now(timezone.utc)))
    typer.echo(f"{control_id} 已签字")
```

- [x] **Step 6: 运行测试确认通过**

Run: `pytest tests/cli/test_interview_render.py -v`
Expected: 7 passed

- [x] **Step 7: 提交**

```bash
git add src/framework_reader/cli/interview.py src/framework_reader/cli/main.py \
        pyproject.toml tests/cli/test_interview_render.py
git commit -m "feat(cli): 访谈终端壳与 \$EDITOR 签字，原话贴在字段上方"
```

---

### Task 15: `fr golden diff`、跨厂商对比与阈值标定

黄金样例 diff 是 W2 的验收标准；跨厂商对比让作者在 W3 前定下厂商组合；
lint 阈值在这一步用实际数据标定，而不是拍一个常数。

**Files:**
- Create: `src/framework_reader/interpret/compare.py`
- Create: `content/lint.yaml`
- Modify: `src/framework_reader/cli/main.py`
- Test: `tests/interpret/test_compare.py`

**Interfaces:**
- Consumes: Task 1/2/3/10/11
- Produces: `FieldDiff`、`diff_against_golden(golden, produced) -> list[FieldDiff]`、`render_diff_table(diffs) -> str`、`cross_provider_extract(clients, *, control_id, questions, answers, models) -> dict[str, dict[str, Field]]`、`LintConfig.load(path) / .threshold`

- [x] **Step 1: 写失败测试**

`tests/interpret/test_compare.py`：

```python
import json

from framework_reader.interpret.compare import (
    LintConfig,
    cross_provider_extract,
    diff_against_golden,
    render_diff_table,
)
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import FakeClient


def _interp(myth: str | None, asks: list[str] | None) -> Interpretation:
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value=myth, basis=Basis.PRACTITIONER)
    fields["auditor_asks"] = Field(value=asks, basis=Basis.PRACTITIONER)
    return Interpretation(control_id="NIST-CSF-2.0:PR.AA-05", fields=fields)


def test_diff_covers_the_three_differentiating_fields_only():
    diffs = diff_against_golden(
        _interp("手写的误解", ["手写追问"]), _interp("产出的误解", ["产出追问"])
    )
    assert [d.field for d in diffs] == list(DIFFERENTIATING_FIELDS)


def test_diff_marks_a_field_the_pipeline_left_empty():
    diffs = diff_against_golden(_interp("手写的误解", ["a"]), _interp(None, ["a"]))
    myth = next(d for d in diffs if d.field == "common_myth")
    assert myth.produced_empty is True
    assert myth.golden_empty is False


def test_diff_reports_length_ratio_as_a_bluntness_signal():
    """产出比手写短一大截，通常意味着塌成了通用表述。W2 spec §8.3"""
    diffs = diff_against_golden(
        _interp("手写的误解很长很具体还举了例子", ["a"]), _interp("很空泛", ["a"])
    )
    myth = next(d for d in diffs if d.field == "common_myth")
    assert myth.length_ratio < 0.5


def test_render_diff_table_names_both_sides():
    table = render_diff_table(diff_against_golden(_interp("手写", ["a"]), _interp("产出", ["a"])))
    assert "手写" in table and "产出" in table and "common_myth" in table


def test_cross_provider_runs_the_same_answers_through_each_client():
    payload = json.dumps(
        {"common_myth": "以为有张权限表就行", "auditor_asks": None, "regional_note": None},
        ensure_ascii=False,
    )
    questions = [Question(n=1, kind="fixed", text="q1"),
                 Question(n=2, kind="fixed", text="q2"),
                 Question(n=3, kind="adaptive", text="q3")]
    answers = [RawAnswer(n=n, text=f"以为有张权限表就行 {n}") for n in (1, 2, 3)]
    result = cross_provider_extract(
        {"deepseek": FakeClient([payload]), "glm": FakeClient([payload])},
        control_id="NIST-CSF-2.0:PR.AA-05",
        questions=questions,
        answers=answers,
        models={"deepseek": "deepseek-chat", "glm": "glm-4-plus"},
    )
    assert set(result) == {"deepseek", "glm"}
    assert result["glm"]["common_myth"].value == "以为有张权限表就行"


def test_lint_config_round_trips(tmp_path):
    path = tmp_path / "lint.yaml"
    path.write_text("bigram_threshold: 0.42\n", encoding="utf-8")
    assert LintConfig.load(path).bigram_threshold == 0.42


def test_lint_config_has_a_shipped_default():
    assert 0.0 <= LintConfig.load().bigram_threshold <= 1.0
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/interpret/test_compare.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.interpret.compare`

- [x] **Step 3: 写配置文件**

`content/lint.yaml`：

```yaml
# 抽取忠实度 lint 的阈值。W2 spec §2.3
#
# 不是拍脑袋的常数：用 `fr lint calibrate` 在 3 条黄金样例的访谈产出上标定——
# 取人工判定为忠实抽取的最低重合度，再下调一档。
# 标定前的占位值偏低，宁可漏报也不要在 W3 一开始就被误报淹没。
bigram_threshold: 0.35
```

- [x] **Step 4: 写最小实现**

`src/framework_reader/interpret/compare.py`：

```python
"""黄金样例 diff、跨厂商对比与 lint 配置。W2 spec §3.5、§8"""
from pathlib import Path

import yaml
from pydantic import BaseModel

from framework_reader.interpret.extractor import extract_fields
from framework_reader.interpret.lint import bigram_overlap
from framework_reader.interpret.model import (
    DIFFERENTIATING_FIELDS,
    Field,
    Interpretation,
    Question,
    RawAnswer,
)
from framework_reader.llm.client import LLMClient

DEFAULT_LINT_PATH = Path("content/lint.yaml")


class LintConfig(BaseModel):
    bigram_threshold: float

    @classmethod
    def load(cls, path: Path = DEFAULT_LINT_PATH) -> "LintConfig":
        return cls(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _text(field: Field) -> str:
    value = field.value
    if value is None:
        return ""
    if isinstance(value, list):
        return " / ".join(str(v) for v in value)
    return str(value)


class FieldDiff(BaseModel):
    field: str
    golden: str
    produced: str
    golden_empty: bool
    produced_empty: bool
    overlap: float
    length_ratio: float


def diff_against_golden(
    golden: Interpretation, produced: Interpretation
) -> list[FieldDiff]:
    diffs: list[FieldDiff] = []
    for name in DIFFERENTIATING_FIELDS:
        g = _text(golden.fields[name])
        p = _text(produced.fields[name])
        diffs.append(FieldDiff(
            field=name,
            golden=g,
            produced=p,
            golden_empty=not g,
            produced_empty=not p,
            overlap=bigram_overlap(p, g) if p else 0.0,
            length_ratio=(len(p) / len(g)) if g else (1.0 if not p else 0.0),
        ))
    return diffs


def render_diff_table(diffs: list[FieldDiff]) -> str:
    lines = ["| 字段 | 手写 | 产出 | 重合 | 长度比 |", "|---|---|---|---|---|"]
    for d in diffs:
        lines.append(
            f"| {d.field} | {d.golden[:40]} | {d.produced[:40]} | "
            f"{d.overlap:.2f} | {d.length_ratio:.2f} |"
        )
    return "\n".join(lines)


def cross_provider_extract(
    clients: dict[str, LLMClient],
    *,
    control_id: str,
    questions: list[Question],
    answers: list[RawAnswer],
    models: dict[str, str],
    failure_dir: Path | None = None,
) -> dict[str, dict[str, Field]]:
    """同一批答案跑多家厂商，供作者在 W3 前定厂商。W2 spec §3.5"""
    return {
        provider: extract_fields(
            client, control_id=control_id, questions=questions,
            answers=answers, model=models[provider], failure_dir=failure_dir,
        )
        for provider, client in sorted(clients.items())
    }
```

- [x] **Step 5: 挂 CLI**

`src/framework_reader/cli/main.py` 中把 Task 3 写的 `golden` 命令替换为：

```python
@app.command("golden")
def golden(action: str = typer.Argument(..., help="validate | diff")) -> None:
    """黄金样例：validate 校验，diff 与访谈产出对比。"""
    from framework_reader.interpret.compare import diff_against_golden, render_diff_table
    from framework_reader.interpret.golden import GOLDEN_CONTROLS, load_golden
    from framework_reader.interpret.store import InterpretationStore

    if action not in ("validate", "diff"):
        typer.echo(f"未知操作：{action}")
        raise typer.Exit(2)

    store = InterpretationStore()
    for control_id in GOLDEN_CONTROLS:
        gold = load_golden(control_id)
        if action == "validate":
            filled = [
                n for n in ("common_myth", "auditor_asks", "regional_note")
                if gold.fields[n].value
            ]
            typer.echo(
                f"{control_id}  签字={gold.provenance.confirmed_by}  差异化字段已填={filled}"
            )
            continue
        if not store.exists(control_id):
            typer.echo(f"{control_id}  尚无访谈产出，先跑 fr interview")
            continue
        typer.echo(f"\n## {control_id}")
        typer.echo(render_diff_table(diff_against_golden(gold, store.load(control_id))))


@app.command("lint")
def lint(action: str = typer.Argument(..., help="calibrate")) -> None:
    """calibrate：在 3 条黄金控制的访谈产出上标定 lint 阈值。"""
    from framework_reader.interpret.golden import GOLDEN_CONTROLS
    from framework_reader.interpret.lint import field_scores, suggest_threshold
    from framework_reader.interpret.store import InterpretationStore

    if action != "calibrate":
        typer.echo(f"未知操作：{action}")
        raise typer.Exit(2)

    store = InterpretationStore()
    scores: list[float] = []
    for control_id in GOLDEN_CONTROLS:
        if not store.exists(control_id):
            continue
        interp = store.load(control_id)
        per_field = field_scores(interp.fields, interp.interview.raw)
        for name, score in sorted(per_field.items()):
            typer.echo(f"{control_id:26} {name:14} {score:.3f}")
            scores.append(score)
    if not scores:
        typer.echo("还没有访谈产出可供标定")
        raise typer.Exit(1)
    typer.echo(
        f"\n先逐条肉眼确认上面这些都是忠实抽取，再把 content/lint.yaml 的 "
        f"bigram_threshold 改为 {suggest_threshold(scores):.2f}"
    )
```

- [x] **Step 6: 让访谈壳用标定出来的阈值**

`src/framework_reader/cli/main.py` 的 `interview` 命令里，把 Task 14 留下的硬编码
`threshold=0.5` 换掉：

```python
    from framework_reader.interpret.compare import LintConfig
    threshold = LintConfig.load().bigram_threshold
    path.write_text(annotated_yaml(interp, scores, threshold=threshold), encoding="utf-8")
```

- [x] **Step 7: 运行测试确认通过**

Run: `pytest tests/interpret/test_compare.py -v`
Expected: 7 passed

- [x] **Step 8: 提交**

```bash
git add src/framework_reader/interpret/compare.py content/lint.yaml \
        src/framework_reader/cli/main.py tests/interpret/test_compare.py
git commit -m "feat(interpret): 黄金样例 diff、跨厂商对比与阈值标定"
```

---

### Task 16: 构建集成——只有签过字的解读进包

**Files:**
- Modify: `src/framework_reader/pack/db.py`
- Modify: `src/framework_reader/pack/build.py`
- Modify: `src/framework_reader/pack/validate.py`
- Modify: `src/framework_reader/query/api.py`
- Test: `tests/pack/test_interpretation_build.py`

**Interfaces:**
- Consumes: Task 1/2、W1 的 `create_schema` / `assert_build_invariants` / `Glossary`
- Produces：
  - DDL 新表 `interpretation(control_id, locale, field, value_json, basis, PRIMARY KEY (control_id, locale, field))`
  - `insert_interpretations(conn, items: list[Interpretation]) -> None`
  - `assert_only_confirmed(items) -> None`（`BuildAssertionError`）
  - `assert_glossary_clean(items, glossary) -> None`
  - `QueryAPI.interpretation(control_id, locale="zh-CN") -> dict[str, Field]`

- [x] **Step 1: 写失败测试**

`tests/pack/test_interpretation_build.py`：

```python
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    DRAFTED_FIELDS,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import create_schema, insert_interpretations
from framework_reader.pack.glossary import Glossary, GlossaryEntry
from framework_reader.pack.validate import (
    BuildAssertionError,
    assert_glossary_clean,
    assert_only_confirmed,
)


def _interp(state=InterpretationState.CONFIRMED, myth="以为有张权限表就行"):
    fields = {n: Field(value="草稿", basis=Basis.INFERRED) for n in DRAFTED_FIELDS}
    fields["practice"] = Field(value={"1": "一", "2": "二", "3": "三"}, basis=Basis.INFERRED)
    for n in DIFFERENTIATING_FIELDS:
        fields[n] = Field(value=None, basis=Basis.PRACTITIONER)
    fields["common_myth"] = Field(value=myth, basis=Basis.PRACTITIONER)
    provenance = InterpretationProvenance()
    if state is InterpretationState.CONFIRMED:
        provenance = InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        )
    return Interpretation(
        control_id="NIST-CSF-2.0:PR.AA-05", state=state,
        fields=fields, provenance=provenance,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_schema(c)
    yield c
    c.close()


def test_interpretation_rows_are_one_per_field(conn):
    insert_interpretations(conn, [_interp()])
    names = [r[0] for r in conn.execute(
        "SELECT field FROM interpretation WHERE control_id = ? ORDER BY field",
        ("NIST-CSF-2.0:PR.AA-05",),
    )]
    assert len(names) == 7


def test_values_round_trip_through_json(conn):
    insert_interpretations(conn, [_interp()])
    row = conn.execute(
        "SELECT value_json, basis FROM interpretation WHERE field = 'common_myth'"
    ).fetchone()
    assert json.loads(row[0]) == "以为有张权限表就行"
    assert row[1] == "practitioner"


def test_locale_column_exists_from_day_one(conn):
    """主 spec §8⑤：locale 从第一天存在，即使当前只有 zh-CN。"""
    insert_interpretations(conn, [_interp()])
    assert conn.execute("SELECT DISTINCT locale FROM interpretation").fetchone()[0] == "zh-CN"


def test_unconfirmed_interpretation_fails_the_build():
    with pytest.raises(BuildAssertionError, match="draft"):
        assert_only_confirmed([_interp(state=InterpretationState.DRAFT)])


def test_confirmed_interpretations_pass():
    assert_only_confirmed([_interp()])


def test_glossary_violation_in_an_interpretation_fails_the_build():
    glossary = Glossary(entries=[GlossaryEntry(
        preferred="控制", banned=["控件"], en="control", rationale="统一术语"
    )])
    with pytest.raises(BuildAssertionError, match="控件"):
        assert_glossary_clean([_interp(myth="他们以为有个控件表就行")], glossary)


def test_clean_interpretations_pass_the_glossary(conn):
    glossary = Glossary(entries=[GlossaryEntry(
        preferred="控制", banned=["控件"], en="control", rationale="统一术语"
    )])
    assert_glossary_clean([_interp()], glossary)
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/pack/test_interpretation_build.py -v`
Expected: FAIL —— `ImportError: cannot import name 'insert_interpretations'`

- [x] **Step 3: 扩 DDL 与写入层**

`src/framework_reader/pack/db.py`：在 `original_text` 表定义之后插入

```sql
-- 解读。只有签过字的进包。W2 spec §4.3
CREATE TABLE interpretation (
    control_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    basis TEXT NOT NULL,
    PRIMARY KEY (control_id, locale, field)
);
```

并在文件末尾追加：

```python
def insert_interpretations(
    conn: sqlite3.Connection, items: list["Interpretation"]
) -> None:
    import json

    rows = [
        (i.control_id, i.locale, name, json.dumps(f.value, ensure_ascii=False), f.basis.value)
        for i in items
        for name, f in sorted(i.fields.items())
    ]
    conn.executemany(
        "INSERT INTO interpretation (control_id, locale, field, value_json, basis) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
```

并在 `db.py` 顶部 import 区加：

```python
from framework_reader.interpret.model import Interpretation
```

- [x] **Step 4: 加构建断言**

`src/framework_reader/pack/validate.py` 末尾追加：

```python
def assert_only_confirmed(items: list["Interpretation"]) -> None:
    """AI 不能签字，未签字的解读不进包。主 spec §5、W2 spec §4.3"""
    from framework_reader.interpret.model import InterpretationState

    bad = [i for i in items if i.state is not InterpretationState.CONFIRMED]
    if bad:
        raise BuildAssertionError(
            f"{len(bad)} 条解读未签字（state={bad[0].state.value}），"
            f"首条：{bad[0].control_id}"
        )
    unsigned = [i for i in items if not (i.provenance.confirmed_by or "").strip()]
    if unsigned:
        raise BuildAssertionError(f"{unsigned[0].control_id} 的 confirmed_by 为空")


def assert_glossary_clean(items: list["Interpretation"], glossary) -> None:
    """术语表覆盖解读文本，不只覆盖 label。主 spec §10.B3"""
    for interp in items:
        for name, field in sorted(interp.fields.items()):
            value = field.value
            if value is None:
                continue
            if isinstance(value, list):
                text = " ".join(str(v) for v in value)
            elif isinstance(value, dict):
                text = " ".join(str(v) for v in value.values())
            else:
                text = str(value)
            hits = glossary.check_text(text)
            if hits:
                raise BuildAssertionError(
                    f"{interp.control_id} 的 {name} 用了禁用词：{hits}"
                )
```

并在 `validate.py` 顶部加：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from framework_reader.interpret.model import Interpretation
```

- [x] **Step 5: 接进构建入口**

`src/framework_reader/pack/build.py`：在 `assert_build_invariants(...)` 调用之前插入

```python
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.pack.db import insert_interpretations
    from framework_reader.pack.glossary import Glossary
    from framework_reader.pack.validate import assert_glossary_clean, assert_only_confirmed

    interpretations = list(InterpretationStore().iter_all())
    assert_only_confirmed(interpretations)
    assert_glossary_clean(interpretations, Glossary.load(Path("content/glossary.zh.yaml")))
    insert_interpretations(conn, interpretations)
```

- [x] **Step 6: 扩 QueryAPI**

`src/framework_reader/query/api.py` 追加：

```python
    def interpretation(self, control_id: str, locale: str = "zh-CN") -> dict[str, dict]:
        """读一条控制的解读。调用方不得直接写 SQL。主 spec §8①"""
        import json

        rows = self._conn.execute(
            "SELECT field, value_json, basis FROM interpretation "
            "WHERE control_id = ? AND locale = ? ORDER BY field",
            (control_id, locale),
        ).fetchall()
        return {
            r["field"]: {"value": json.loads(r["value_json"]), "basis": r["basis"]}
            for r in rows
        }
```

- [x] **Step 7: 运行全量测试与真实构建**

Run:
```bash
pytest -v
fr build
```
Expected: 全绿；`fr build` 成功（`content/interpretations/` 此刻为空或只有已签字文件）

- [x] **Step 8: 提交**

```bash
git add src/framework_reader/pack/ src/framework_reader/query/api.py \
        tests/pack/test_interpretation_build.py
git commit -m "feat(pack): 解读进包——只收签过字的，术语表覆盖解读文本"
```

---

### Task 17: 模型调用退避重试

W2 spec §6：模型调用失败要退避重试；仍失败则保留 raw，`fr interview --resume` 续。
重试包在 guard **里面**——红线断言只跑一次且必须在任何请求发出之前拦住。

**Files:**
- Create: `src/framework_reader/llm/retry.py`
- Modify: `src/framework_reader/llm/registry.py`
- Test: `tests/llm/test_retry.py`

**Interfaces:**
- Consumes: Task 4 `LLMClient`/`Message`、Task 7 `LLMRegistry`
- Produces: `RetryingClient(inner, *, attempts=3, base_delay=1.0, sleep=time.sleep)`；`LLMRegistry.build` 内部改为 `GuardedClient(RetryingClient(adapter), guard)`

- [x] **Step 1: 写失败测试**

`tests/llm/test_retry.py`：

```python
import pytest

from framework_reader.llm.client import Message
from framework_reader.llm.guard import GuardedClient, OutboundTextError, PayloadGuard
from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
from framework_reader.llm.retry import RetryingClient

MSG = [Message(role="user", content="hi")]


class _Flaky:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.attempts = 0

    def complete(self, system, messages, *, model, max_tokens=4096):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("厂商 503")
        return "终于成功"


def test_transient_failure_is_retried():
    inner = _Flaky(fail_times=2)
    client = RetryingClient(inner, attempts=3, sleep=lambda _: None)
    assert client.complete("s", MSG, model="m") == "终于成功"
    assert inner.attempts == 3


def test_gives_up_after_the_configured_attempts():
    inner = _Flaky(fail_times=99)
    client = RetryingClient(inner, attempts=3, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="503"):
        client.complete("s", MSG, model="m")
    assert inner.attempts == 3


def test_backoff_grows():
    slept: list[float] = []
    client = RetryingClient(_Flaky(99), attempts=4, base_delay=1.0, sleep=slept.append)
    with pytest.raises(RuntimeError):
        client.complete("s", MSG, model="m")
    assert slept == [1.0, 2.0, 4.0]


def test_red_line_violation_is_never_retried():
    """出口红线抛异常后不重试、不降级——W2 spec §3.4③、§6"""
    class _Guarded:
        def __init__(self) -> None:
            self.attempts = 0

        def complete(self, system, messages, *, model, max_tokens=4096):
            self.attempts += 1
            raise OutboundTextError("受版权原文即将出圈")

    inner = _Guarded()
    with pytest.raises(OutboundTextError):
        RetryingClient(inner, attempts=3, sleep=lambda _: None).complete(
            "s", MSG, model="m"
        )
    assert inner.attempts == 1


def test_registry_puts_retry_inside_the_guard():
    """红线断言只跑一次，且在任何请求发出之前。"""
    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    client = registry.build(
        "drafter", guard=PayloadGuard([]),
        key_lookup=lambda name: "sk-test",
    )
    assert isinstance(client, GuardedClient)
    assert isinstance(client._inner, RetryingClient)
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/llm/test_retry.py -v`
Expected: FAIL —— `ModuleNotFoundError: framework_reader.llm.retry`

- [x] **Step 3: 写最小实现**

`src/framework_reader/llm/retry.py`：

```python
"""退避重试。W2 spec §6

红线异常不重试——那不是瞬时故障，是设计违规。
"""
import time
from collections.abc import Callable

from framework_reader.llm.client import LLMClient, Message
from framework_reader.llm.guard import OutboundTextError


class RetryingClient:
    def __init__(
        self,
        inner: LLMClient,
        *,
        attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base_delay = base_delay
        self._sleep = sleep

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                return self._inner.complete(
                    system, messages, model=model, max_tokens=max_tokens
                )
            except OutboundTextError:
                raise                      # 红线不是瞬时故障，一次也不重试
            except Exception as exc:
                last = exc
                if attempt < self._attempts - 1:
                    self._sleep(self._base_delay * (2**attempt))
        assert last is not None
        raise last
```

- [x] **Step 4: 接进 registry**

`src/framework_reader/llm/registry.py`：把 `build` 的最后两行

```python
        return GuardedClient(inner, guard)
```

改为

```python
        from framework_reader.llm.retry import RetryingClient

        # 重试在 guard 里面：红线断言只跑一次，且在任何请求发出之前。
        return GuardedClient(RetryingClient(inner), guard)
```

- [x] **Step 5: 运行测试确认通过**

Run: `pytest tests/llm -v`
Expected: 全绿（Task 7 的 `test_build_picks_the_adapter_matching_the_preset_kind` 需同步改为断言 `client._inner._inner` 的类型）

`tests/llm/test_registry.py` 中该用例改为：

```python
def test_build_picks_the_adapter_matching_the_preset_kind():
    drafter = REG.build("drafter", guard=PayloadGuard([]), key_lookup=KEYS)
    extractor = REG.build("extractor", guard=PayloadGuard([]), key_lookup=KEYS)
    assert isinstance(drafter._inner._inner, AnthropicClient)
    assert isinstance(extractor._inner._inner, OpenAICompatClient)
```

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/llm/retry.py src/framework_reader/llm/registry.py \
        tests/llm/test_retry.py tests/llm/test_registry.py
git commit -m "feat(llm): 退避重试，红线异常不重试"
```

---

### Task 18: 签字内容摘要断言

W2 spec §4.3：**已 `confirmed` 的文件再被编辑，必须重新签字。**

> **本 task 已在实施中修正。** 计划初稿写的是「`confirmed_at` 早于文件 mtime 即失败」，
> 那是错的：git 恢复文件时打的是当前时间，`git clone` / CI checkout / 切分支之后 mtime
> 一律是「刚刚」，会把全部已签字条目误判为「签字后被改过」。已实测复现。
> 正确做法是**内容摘要**：签字时算 sha256 存进 `provenance.signed_digest`，构建期重算比对。
> 与文件时间戳无关，clone 不受影响，签字后改内容照样抓得住。

**Files:**
- Modify: `src/framework_reader/interpret/model.py`（`signed_digest` / `interview_seconds` / `fields_digest`）
- Modify: `src/framework_reader/cli/interview.py`（`sign` 打摘要）
- Modify: `src/framework_reader/pack/validate.py`（`assert_signature_matches_content`）
- Modify: `src/framework_reader/pack/build.py`
- Test: `tests/pack/test_signature_digest.py`

**Interfaces:**
- Consumes: Task 1 `Interpretation`、Task 14 `sign`
- Produces: `fields_digest(interp) -> str`、`InterpretationProvenance.signed_digest`、
  `InterpretationProvenance.interview_seconds`、`assert_signature_matches_content(items) -> None`

关键设计点：

1. 摘要覆盖 `control_id` + `locale` + `fields` + `interview`（作者原话是签字时一并认下的证据）。
2. 摘要**不含 `provenance`**——否则事后补记 `interview_seconds` 会让签字失效。
3. 缺 `signed_digest` 直接失败，不做「老文件豁免」：豁免会变成永久后门。
4. 回归测试必须包含一条**真 `git clone`** 的用例（签于一周前 → clone → 构建应通过），
   否则这个坑会被重新踩回来。

### Task 19: 回改主 spec 五处

W2 spec §10 列的修订项。**这一步不改代码，但不做就会让主 spec 与实现长期背离**——
下一个读 spec 的人（包括三个月后的作者）会按旧描述做决定。

**Files:**
- Modify: `docs/superpowers/specs/2026-08-19-framework-reader-design.md`

**Interfaces:**
- Consumes: 无
- Produces: 无（文档一致性）

- [x] **Step 1: §7.2 —— 「审核 TUI」改为「访谈 TUI」**

在 §7.2 工期表 `W1–W2` 一行的内容里，把「审核 TUI」改为「访谈 TUI」，并在该表下方补一段：

```markdown
**W2 的方法论修订（见 `docs/superpowers/specs/2026-08-20-w2-interview-pipeline-design.md`）**：
`common_myth` / `auditor_asks` / `regional_note` 三个差异化字段**不由 AI 起草**，改为
AI 提问（每条 3 问）、作者回答、严格抽取。原因：若这三个字段也是 AI 稿，W3 盲测比的将是
「带提示词的大模型」对「裸大模型」，通过线过不去，且无法区分「方法论不成立」与「管线设计错了」。
```

- [x] **Step 2: §3.4 —— `basis` 增加 `practitioner`**

把 §3.4 末尾「每个字段附带 `basis`：该表述依据原文哪一句，或标记为 `inferred`」改为：

```markdown
每个字段附带 `basis`，取值三选一：`quote:<原文定位>`（依据原文哪一句）、`inferred`（模型推断）、
`practitioner`（作者的从业经验）。三个差异化字段恒为 `practitioner`——盲测未通过时，这是回溯
「哪些字段是人给的、哪些是模型给的」的唯一依据。
```

- [x] **Step 3: §5 —— 离线生产的「禁止」增加一条**

在 §5 表格「离线生产」行的「禁止」栏，把内容改为：

```
直接落库（必须过人审）；**为 common_myth / auditor_asks / regional_note 三个差异化字段起草**
```

- [x] **Step 4: §10.A —— 红线增加第三条**

在 §10.A 的红线列表末尾追加：

```markdown
3. **Tier C/D 原文不得进入任何模型调用的 payload。** 所有 client 由 `llm/registry.py` 组装
   并被 `GuardedClient` 包住，出网只有这一条路径；命中即抛异常，不重试、不降级、不降为 warn。
```

- [x] **Step 5: §7.4 —— 止损线表格增加一行**

在 §7.4 的表格中追加：

```markdown
| 黄金样例 diff 显示访谈产出空泛（三个差异化字段有两个塌成通用表述） | **W2 停下来重新设计访谈**，不带着坏管线冲进 W3 |
```

- [x] **Step 6: 确认无残留旧表述**

Run:
```bash
grep -n "审核 TUI" docs/superpowers/specs/2026-08-19-framework-reader-design.md
grep -n "或标记为 \`inferred\`" docs/superpowers/specs/2026-08-19-framework-reader-design.md
```
Expected: 两条命令都无输出

- [x] **Step 7: 提交**

```bash
git add docs/superpowers/specs/2026-08-19-framework-reader-design.md
git commit -m "docs: 主 spec 随 W2 修订——访谈 TUI、practitioner basis、第三条红线"
```

---

### Task 20: CI 断言与 W2 收尾

**Files:**
- Create: `tests/test_no_network_in_tests.py`
- Modify: `README.md`
- Modify: `Makefile`
- Test: 本 task 即测试

**Interfaces:**
- Consumes: 前述全部
- Produces: 可在无 API key、无网络环境下通过的测试套件

- [x] **Step 1: 写测试**

`tests/test_no_network_in_tests.py`：

```python
from pathlib import Path

SELF = "test_no_network_in_tests.py"


def _test_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in Path("tests").rglob("*.py")]


def test_no_test_reads_the_process_environment():
    """公有 CI 不接触 API key。测试要注入假 lookup，不许读环境。W2 spec §7

    注意：断言的是「有没有读环境」，不是「有没有出现 KEY 这个词」——
    tests/llm/test_registry.py 会把环境变量名当作假 lookup 的字典键，那是对的。
    """
    offenders = [
        str(path) for path, text in _test_sources()
        if ("os.environ" in text or "os.getenv" in text) and path.name != SELF
    ]
    assert offenders == [], f"这些测试读了进程环境：{offenders}"


def test_no_test_calls_the_live_check_command():
    """`fr llm check` 会发真实请求，测试永远不得调用它。"""
    offenders = [
        str(path) for path, text in _test_sources()
        if ("_default_post" in text or "_default_send" in text) and path.name != SELF
    ]
    assert offenders == [], f"这些测试碰了真实出网路径：{offenders}"


def test_no_test_imports_a_vendor_sdk_at_module_level():
    """anthropic / httpx 只在适配器内部按需 import；测试导入它们说明走错了路。"""
    offenders = [
        str(path) for path, text in _test_sources()
        if ("import anthropic" in text or "import httpx" in text) and path.name != SELF
    ]
    assert offenders == [], f"这些测试导入了厂商 SDK：{offenders}"


def test_content_dirs_that_must_exist_are_present():
    assert Path("content/llm_providers.yaml").exists()
    assert Path("content/lint.yaml").exists()
    assert Path("content/golden/NIST-CSF-2.0").is_dir()
```

- [x] **Step 2: 运行测试确认状态**

Run: `pytest tests/test_no_network_in_tests.py -v`
Expected: 4 passed（若失败，说明某个测试硬编码了 key 或真实出网路径——改成用 `FakeClient`）

- [x] **Step 3: 在无 key 环境验证 CI 假设**

Run:
```bash
env -u ANTHROPIC_API_KEY -u DEEPSEEK_API_KEY -u OPENAI_API_KEY pytest -q
```
Expected: 全量测试仍全绿

- [x] **Step 4: 更新 README**

`README.md` 的「开发」一节替换为：

```markdown
## 开发

```bash
make install     # 安装依赖（含 dev）
make test        # 跑代码测试（不需要 vendor/，不需要 API key）
./scripts/fetch_sources.sh   # 取回 NIST 公共领域源文件到 vendor/
make build       # 构建 build/content.sqlite（需要 vendor/）
fr stats         # 查看图谱统计
fr show NIST-CSF-2.0:DE.CM-01
```

## 解读生产（W2）

```bash
fr llm check                 # 逐个 ping 厂商预设，验活（发真实请求，手工跑）
fr golden validate           # 校验 3 条手写黄金样例
fr draft --all               # 批量起草四个非差异化字段（离线可并发）
fr interview --next          # 访谈下一条：三问三答 → 抽取 → $EDITOR 签字
fr golden diff               # 访谈产出 vs 手写黄金样例
fr lint calibrate            # 标定抽取忠实度阈值
```

## 三条不可越过的红线

1. **受版权标准原文永不进入本仓库。** `vendor/` 已在 `.gitignore` 中；
   构建时断言 `original_text` 表为空。
2. **映射来源必须登记在 `content/allowed_sources.yaml`。**
   白名单以「NIST 署名文件」为单位，域名本身不构成允许理由。
3. **Tier C/D 原文不得进入任何模型调用的 payload。**
   所有 client 由 `llm/registry.py` 组装并被 `GuardedClient` 包住，出网只有这一条路径。
```

- [x] **Step 5: 更新 Makefile**

追加：

```make
.PHONY: draft interview

draft:
	fr draft --all

interview:
	fr interview --next
```

- [x] **Step 6: 提交**

```bash
git add tests/test_no_network_in_tests.py README.md Makefile
git commit -m "ci: 测试零网络零 key 断言，README 补第三条红线"
```

- [ ] **Step 7: W2 验收**

逐项确认（W2 spec §8.1）：

```bash
pytest -v                                    # 全绿
env -u ANTHROPIC_API_KEY -u DEEPSEEK_API_KEY pytest -q   # 无 key 仍全绿
fr golden validate                           # 3 条黄金样例齐、已签字、零 AI 痕迹
fr llm check                                 # 手工：至少 drafter/questioner/extractor 三家 OK
fr draft --all                               # 批量起草跑通
fr interview NIST-CSF-2.0:GV.SC-07           # 3 条黄金控制各跑一遍完整访谈
fr interview NIST-CSF-2.0:PR.AA-05
fr interview NIST-CSF-2.0:GV.RM-02
fr golden diff                               # 出对比表（按 W2 spec §8.0 单向解读）
fr lint calibrate                            # 标定阈值并写回 content/lint.yaml

# 判定管线的地方：作者从未写过的控制，冷启动。W2 spec §8.2
fr interview NIST-CSF-2.0:DE.CM-01           # 技术域，证据形态明确
fr interview NIST-CSF-2.0:GV.OV-01           # 治理复核，务虚

fr build                                     # 构建断言全过
```

W2 完成标准（对应 W2 spec §8.1）：

- [x] 3 条黄金样例手写完毕，零 AI，`fr golden validate` 三行全绿
- [x] `fr golden diff` 出表。**单向解读**（W2 spec §8.0）：变坏 → 触发止损；好看 → 只证明抽取器没乱改，不证明追问设计成立
- [x] 跨厂商对比表出来，W3 的厂商组合已写回 `content/llm_providers.yaml` 的 `roles`
- [x] `content/lint.yaml` 的阈值已用黄金控制的数据标定，不再是占位值
- [ ] **2–3 条陌生控制冷启动跑完**，逐条读：三个差异化字段里有没有一句是通用大模型说不出来的？两个字段塌成通用表述 → 触发 W2 spec §8.3 止损，停下重新设计访谈，不进 W3
- [ ] **每条耗时已记录**（`fr interview` 自动打印并写入 `provenance.interview_seconds`），并据此决定 W3 是 106 条还是砍到 50 条（主 spec §7.4）
- [x] 全部测试绿，且在无 API key、无网络环境下绿

```bash
git add -A && git commit -m "chore: W2 验收——访谈管线完成"
```

---

## W2 之后

W3 接主 spec §7.2：完成 CSF 106 条 L-Full 审核，然后**立即跑第一轮盲测**（§7.3），不等 ISO。

盲测的两条硬约束，现在记下：

1. **不得由 AI 判定。** R7 那次是 AI 自评（结论采信，因为 17% 离 30% 线足够远且失败模式是结构性的），盲测不行——它验的正是「人觉得哪份更有用」。
2. **通过线事前定死、事后不得修改**：≥ 70% 场次选本产品的 Interpretation，且至少 2 人主动指出
   `common_myth` / `auditor_asks` / `regional_note` 有价值。

另需记账（W2 不处理）：

- **OLIR #186 文件名含 `_draft`**，贡献 1182 条可导出边中的 743 条。W6 打包发布前必须回 OLIR 目录页重新核实，结论更新进 `content/allowed_sources.yaml`。
- **`QueryAPI.search()` 只匹配 `label`**，中文关键词当前只能命中 ISO 93 条。W2 有解读文本入库后，W4 必须重做检索。
