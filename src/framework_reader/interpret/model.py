"""解读的 schema 与状态机。W2 spec §4.2、§4.3；主 spec §3.4"""
import hashlib
import json
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
    # 一个字都没进字段的答案编号。落不进可以，静默消失不行。
    unplaced: list[int] = []


class ModelRef(BaseModel):
    provider: str
    model: str
    prompt_version: str


class InterpretationProvenance(BaseModel):
    drafter: ModelRef | None = None
    extractor: ModelRef | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    # 签字时内容的摘要。构建期重算比对——mtime 不是内容有没有变的证据，
    # git clone / CI checkout / 切分支都会刷新它。W2 spec §4.3
    signed_digest: str | None = None
    # 本条访谈实际花了多少秒。W2 spec §8.2：这个数字决定 W3 的范围。
    interview_seconds: float | None = None
    # 换版继承的来源条款。继承产物永远是 draft：签字是旧条款的，必须重签。
    inherited_from: str | None = None


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
            raise ValueError(f"field set must be exactly the seven fields; missing {sorted(missing)}, extra {sorted(extra)}")

        # B 路线（2026-08-20）：七个字段均可由 AI 撰写，标 inferred；作者亲手写或
        # 改过的标 practitioner。两者都合法——`basis` 记的是谁写的。主 spec §5
        # 但 quote 不合法：原文里没有「误解」「审计员追问」「地域差异」这种东西，
        # 标 quote 要么是建模错了，要么是在伪造出处。
        for name in DIFFERENTIATING_FIELDS:
            if self.fields[name].basis is Basis.QUOTE:
                raise ValueError(
                    f"{name} cannot be grounded in the original text; basis must not be quote"
                )

        if self.state is InterpretationState.CONFIRMED:
            signer = (self.provenance.confirmed_by or "").strip()
            if not signer or self.provenance.confirmed_at is None:
                raise ValueError("confirmed must record confirmed_by and confirmed_at")
            if signer.startswith("ai:") or signer.startswith("model:"):
                raise ValueError(f"AI cannot sign: confirmed_by={signer} (main spec §5)")
        return self


def fields_digest(interp: "Interpretation") -> str:
    """签字覆盖的内容：控制编号、locale、七个字段、以及作者原话。

    不含 provenance——这样事后补记耗时不会让签字失效。
    """
    payload = interp.model_dump(mode="json")
    canonical = {
        "control_id": payload["control_id"],
        "locale": payload["locale"],
        "fields": payload["fields"],
        "interview": payload["interview"],
    }
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
