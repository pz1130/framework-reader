"""核心实体。spec §3.1"""
from enum import Enum

from pydantic import BaseModel, model_validator


class LicenseTier(str, Enum):
    """语料授权分层。spec §4.1"""

    A_EMBEDDABLE = "A"       # 可完整内置（公共领域）
    B_NO_REDIST = "B"        # 免费可得但不可再分发
    C_PURCHASE = "C"         # 必须购买
    D_NO_COMMERCIAL = "D"    # 商用完全不可用
    # 用户自己导入的框架（公司内部制度等）。永远不进我们发布的内容包，
    # 也永远不由我们分发；起草时可以用他自己的文本、他自己的 key。主 spec §7.3.5
    U_USER = "U"


class ControlStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class SupersedeRelation(str, Enum):
    """废止条目的去向。OSCAL catalog 用连字符，CSF 用下划线，此处归一。"""

    INCORPORATED_INTO = "incorporated_into"   # 内容被并入另一条（可能并入多条）
    MOVED_TO = "moved_to"                     # 同一内容换了编号


class Framework(BaseModel):
    id: str
    name: str
    version: str
    tier: LicenseTier
    source_url: str
    license_note: str


class FrameworkControl(BaseModel):
    id: str
    framework_id: str
    parent_id: str | None = None
    label: str
    label_is_original: bool
    framework_tier: LicenseTier
    status: ControlStatus = ControlStatus.ACTIVE

    @model_validator(mode="after")
    def _forbid_original_label_outside_tier_a(self) -> "FrameworkControl":
        # 只有 Tier A（公共领域）允许直接使用官方标题原文。spec §4.1
        if self.label_is_original and self.framework_tier is not LicenseTier.A_EMBEDDABLE:
            raise ValueError(
                f"label_is_original=True is only allowed for Tier A; {self.framework_id} is "
                f"Tier {self.framework_tier.value}; label must be self-written"
            )
        return self


class Supersession(BaseModel):
    """一条废止控制与其去向之间的关系。spec §8②

    多对多：一条可以被拆进多条，一条也可以吸收多条旧编号——因此不能做成
    FrameworkControl 上的单值字段。
    """

    old_id: str
    new_id: str
    relation: SupersedeRelation

    @model_validator(mode="after")
    def _reject_self_reference(self) -> "Supersession":
        if self.old_id == self.new_id:
            raise ValueError("old_id and new_id must not be the same")
        return self


class UnifiedControl(BaseModel):
    """枢纽层。spec §3.2①：内容上 1:1 复制自 CSF 2.0，schema 上完全独立。"""

    id: str
    label: str
    locale: str
