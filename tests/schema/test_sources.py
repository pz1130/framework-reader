from pathlib import Path

import pytest

from framework_reader.schema.sources import DisallowedSourceError, SourceRegistry

REGISTRY = Path("content/allowed_sources.yaml")


def test_nist_signed_files_are_allowed():
    reg = SourceRegistry.load(REGISTRY)
    assert reg.is_allowed("NIST-CPRT-csf-pf-to-sp800-53r5")
    assert reg.is_allowed("NIST-SP800-53r5-to-iso-27001")
    assert reg.is_allowed("NIST-OSCAL-sp800-53r5-catalog")


@pytest.mark.parametrize(
    "source",
    [
        "SCF-2026.1",                       # CC BY-ND
        "CIS-Controls-v8",                  # CC BY-NC-ND
        "PCI-DSS-4.0-official-mapping",     # PCI SSC 条款
        "CSA-CCM-v4",                       # 商用需授权
        "OLIR-third-party-somevendor",      # 第三方提交件
    ],
)
def test_restricted_sources_are_rejected(source):
    reg = SourceRegistry.load(REGISTRY)
    assert reg.is_allowed(source) is False
    with pytest.raises(DisallowedSourceError, match=source):
        reg.assert_allowed(source)


def test_pci_denied_reason_is_explicit():
    """PCI 版本化 source id 应对上 denied 前缀，抛出明确 PCI SSC 禁令而非泛白名单句。"""
    reg = SourceRegistry.load(REGISTRY)
    with pytest.raises(DisallowedSourceError, match="PCI SSC") as exc_info:
        reg.assert_allowed("PCI-DSS-4.0-official-mapping")
    assert "PCI-DSS-4.0-official-mapping" in str(exc_info.value)
    assert "not on the allowlist" not in str(exc_info.value)


def test_domain_alone_is_not_a_reason():
    """csrc.nist.gov 域名不构成允许理由——第三方 OLIR 也在该域名下。spec §4.3、§10.A"""
    reg = SourceRegistry.load(REGISTRY)
    assert reg.is_allowed("https://csrc.nist.gov/anything-unregistered") is False


def test_derived_and_ai_pseudo_sources_are_allowed():
    """推导边与 AI 边不来自外部语料，其伪来源需在白名单内以便入库。"""
    reg = SourceRegistry.load(REGISTRY)
    assert reg.is_allowed("derived:two-hop")
    assert reg.is_allowed("ai:claude-opus-5")


def test_every_entry_records_license_and_checked_on():
    reg = SourceRegistry.load(REGISTRY)
    for entry in reg.entries:
        assert entry.license, f"{entry.id} 缺 license"
        assert entry.checked_on, f"{entry.id} 缺 checked_on"
