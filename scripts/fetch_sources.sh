#!/usr/bin/env bash
# 取回 NIST 公共领域源文件到 vendor/（vendor/ 不进 Git）
# 仅限白名单内的 NIST 署名文件。见 content/allowed_sources.yaml
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor/nist

echo "==> OSCAL SP 800-53 Rev5 catalog"
curl -fL -o vendor/nist/sp800-53r5-catalog.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json

echo "==> CSF 1.1 + Privacy Framework ↔ SP 800-53 Rev5 (SP 800-53 supplemental xlsx)"
# 发布页此文件仍在；打开后标题为 CSF Version 1.1，不是 2.0。
curl -fL -o vendor/nist/csf-pf-to-sp800-53r5-mappings.xlsx \
  https://csrc.nist.gov/files/pubs/sp/800/53/r5/upd1/final/docs/csf-pf-to-sp800-53r5-mappings.xlsx

echo "==> CSF 2.0 ↔ SP 800-53 Rev 5.2.0 (NIST OLIR #186, developer=NIST, Owner)"
curl -fL -o vendor/nist/csf-2.0-to-sp800-53r5-mappings.xlsx \
  https://csrc.nist.gov/csrc/media/projects/olir/documents/submissions/Cybersecurity_Framework_v2-0_Concept_Crosswalk_800-53_5_2_0_draft.xlsx

echo "==> SP 800-53 Rev5 ↔ ISO/IEC 27001:2022 mapping (NIST OLIR; developer=NIST)"
# 原 docx（CSRC/media/.../sp800-53r5-to-iso-27001-mapping.docx）已 301→404。
# 现行 NIST 署名文件是 OLIR #155 xlsx，见 SP 800-53 Rev.5 发布页 Crosswalk 链接。
curl -fL -o vendor/nist/sp800-53r5-to-iso-27001-mapping.xlsx \
  https://csrc.nist.gov/csrc/media/Projects/olir/documents/submissions/sp800-53r5-to-iso-27001-mapping-2022-OLIR-2023-10-12-UPDATED.xlsx

echo "==> CSF 2.0 结构（NIST 署名 OSCAL catalog）"
# CPRT 导出 JSON 端点目前 404；OSCAL catalog 是 usnistgov/oscal-content 上的 NIST 署名文件。
curl -fL -o vendor/nist/csf-2.0.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json

ls -la vendor/nist/
