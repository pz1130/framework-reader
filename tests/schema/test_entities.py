import pytest
from pydantic import ValidationError

from framework_reader.schema.entities import (
    ControlStatus,
    Framework,
    FrameworkControl,
    LicenseTier,
    SupersedeRelation,
    Supersession,
    UnifiedControl,
)


def test_tier_c_framework_must_not_allow_original_labels():
    """Tier C（必须购买）的控制不得声称 label 取自原文。spec §4.1"""
    iso = Framework(
        id="ISO-27002-2022",
        name="ISO/IEC 27002:2022",
        version="2022",
        tier=LicenseTier.C_PURCHASE,
        source_url="https://www.iso.org/standard/75652.html",
        license_note="须购买；原文不可再分发",
    )
    assert iso.tier is LicenseTier.C_PURCHASE

    with pytest.raises(ValidationError, match="label_is_original"):
        FrameworkControl(
            id="ISO-27002-2022:A.8.16",
            framework_id="ISO-27002-2022",
            parent_id=None,
            label="监控活动",
            label_is_original=True,   # 非法：Tier C 不得使用原文标题
            framework_tier=LicenseTier.C_PURCHASE,
        )


def test_tier_a_framework_may_use_original_labels():
    ctl = FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01",
        framework_id="NIST-CSF-2.0",
        parent_id="NIST-CSF-2.0:DE.CM",
        label="Networks and network services are monitored",
        label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE,
    )
    assert ctl.status is ControlStatus.ACTIVE


def test_unified_control_requires_locale():
    uc = UnifiedControl(id="UC:DE.CM-01", label="网络与网络服务的监控", locale="zh-CN")
    assert uc.locale == "zh-CN"

    with pytest.raises(ValidationError):
        UnifiedControl(id="UC:DE.CM-01", label="x")  # 缺 locale


def test_supersession_rejects_self_reference():
    """一条控制不能取代自己——那是解析出了错，不是数据。"""
    with pytest.raises(ValidationError):
        Supersession(
            old_id="NIST-CSF-2.0:DE.CM-04",
            new_id="NIST-CSF-2.0:DE.CM-04",
            relation=SupersedeRelation.MOVED_TO,
        )
