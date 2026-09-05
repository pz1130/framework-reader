# tests/fixtures/

小样本均从 `scripts/fetch_sources.sh` 取回的 **NIST 署名、公共领域** 文件截取。可以进 Git。

**不要**把 ISO / PCI / CIS 的任何原文做成夹具。ISO 对照只存在 `vendor/nist/`（不进 Git）。

全量源文件（不进 Git，见 `vendor/README.md`）：

| 本地路径 | 取自 |
|---|---|
| `vendor/nist/sp800-53r5-catalog.json` | usnistgov/oscal-content `NIST_SP-800-53_rev5_catalog.json` |
| `vendor/nist/csf-2.0.json` | usnistgov/oscal-content `NIST_CSF_v2.0_catalog.json` |
| `vendor/nist/csf-pf-to-sp800-53r5-mappings.xlsx` | SP 800-53 Rev.5 发布页随附 xlsx（**CSF 1.1** + Privacy Framework） |
| `vendor/nist/csf-2.0-to-sp800-53r5-mappings.xlsx` | NIST OLIR #186（CSF 2.0 ↔ 800-53 Rev 5.2.0） |
| `vendor/nist/sp800-53r5-to-iso-27001-mapping.xlsx` | NIST OLIR #155（原 `.docx` 已 404） |

---

## 1. `oscal_800-53r5_sample.json`

来源：`vendor/nist/sp800-53r5-catalog.json`（OSCAL catalog，title: *Electronic (OSCAL) Version of NIST SP 800-53 Rev 5.2.0 Controls and SP 800-53A Rev 5.2.0 Assessment Procedures*）。

观察到的结构（不要猜）：

- 顶层键：`catalog.uuid`、`catalog.metadata`、`catalog.groups`、`catalog.back-matter`
- 20 个 group，`id` 依次为：`ac` `at` `au` `ca` `cm` `cp` `ia` `ir` `ma` `mp` `pe` `pl` `pm` `ps` `pt` `ra` `sa` `sc` `si` `sr`
- group 键：`id`、`class`、`title`、`props`、`controls`
- 控制键（示例 `ac-1`）：`id`、`class`、`title`、`params`、`props`、`links`、`parts`
- 嵌套 enhancement：父控制的 `controls[]`。`id` 形如 `ac-2.1`（不是 `ac-2(1)`）。`ac-1` 无嵌套；`ac-2` 有 13 条。

夹具截取：前 2 个 group（`ac`、`at`），每组前 3 条顶层控制，**保留其全部嵌套 enhancement**（39 条）。足够测 `parent_id`。

---

## 2. `csf_2.0_oscal_sample.json` 与 `csf_2.0_subcategories.json`

来源：`vendor/nist/csf-2.0.json`（OSCAL catalog，title: *Electronic Version of NIST Cybersecurity Framework 2.0*）。

观察到的结构：

- 6 个 function group：`GV` `ID` `PR` `DE` `RS` `RC`
- group `class=function` → 子控制 `class=category`（如 `DE.CM`）→ 孙控制 `class=subcategory`（如 `DE.CM-01`）
- subcategory 的 `title` 只是 ID 本身（如 `"DE.CM-01"`）
- **官方 outcome 文本**在 `parts[]` 中 `name == "statement"` 的 `prose`
- `props` 含 `label`、`sort-id`；已撤回的 subcategory 另有 `props.name == "status"`、`value == "withdrawn"`
- 全量：`class=subcategory` 185 条 = **106 active + 79 withdrawn**

`csf_2.0_oscal_sample.json`：只保留 `DE` function（含 `DE.CM-01`）。

`csf_2.0_subcategories.json`：从同一 catalog 抽出全部 subcategory 的 `id` / `parent_id` / `function_id` / `statement` / `status`。后续 `fr show NIST-CSF-2.0:DE.CM-01` 依赖这些 ID 与 outcome 文本。

`DE.CM-01` 官方 statement（逐字）：

```
Networks and network services are monitored to find potentially adverse events
```

CPRT 的 `.../export/json` 目前 404；结构改从 NIST 署名 OSCAL catalog 取。

---

## 3. `csf_to_800-53_sample.xlsx`  ← Task 8 用这个

**必须用 CSF 2.0 映射（OLIR #186）。** 发布页那个 `csf-pf-to-sp800-53r5-mappings.xlsx` 打开后是 **CSF 1.1**（见 §4），ID 是 `ID.AM-1` 不是 `ID.AM-01`。

来源：`vendor/nist/csf-2.0-to-sp800-53r5-mappings.xlsx`

- **sheet 名（逐字）：** `Relationships`
- **表头行号（Excel 1-based）：** `1`
- **数据从第 2 行起。** 夹具 = 表头 + 前 12 个数据行 + 1 行全空行（验证解析器跳过空行）
- **列名（逐字，含单元格内换行）：**

| 列 | 精确字符串（Python `repr`） |
|---|---|
| A | `'Focal Document\nElement'` |
| B | `'Focal Document Element Description'` |
| C | `'Reference Document\nElement'` |
| D | `'Reference Document\nElement Description\n(Optional)'` |
| E | `'Comments\n(Optional)'` |
| F | `'Strength of\nRelationship\n(Optional)'` |

列 A 是 CSF 2.0 元素 ID（`GV` / `GV.OC` / `GV.OC-01`）。列 C 是 800-53 控制号（如 `PM-11`、`SI-04`）。Function/Category 行的列 C 为空，应跳过。部分控制号单元格带尾随换行（如 `PM-09\n`、`AC-01\n`），解析时需 `strip()`。

全量文件 772 行数据；`DE.CM-01` 映射到 `AC-02`、`AU-12`、`CA-07`、`CM-03`、`SC-05`、`SC-07`、`SI-04`。

Task 8 计划里的占位常量是错的，应改为：

```python
SHEET = "Relationships"
HEADER_ROW = 1
COL_CSF = "Focal Document\nElement"
COL_53 = "Reference Document\nElement"
```

白名单 id：`NIST-OLIR-csf-2.0-to-sp800-53r5`（NIST 署名 OLIR，developer=NIST，Owner，Final）。不是第三方 OLIR。

---

## 4. 发布页 xlsx 实际是 CSF 1.1（不要当 2.0 用）

文件：`vendor/nist/csf-pf-to-sp800-53r5-mappings.xlsx`

- sheets：`README`、`CSF to SP 800-53r5`、`PF to SP 800-53r5`
- sheet `CSF to SP 800-53r5`：
  - 第 1 行：标题 *NIST Cybersecurity Framework Version 1.1 to NIST Special Publication 800-53, Revision 5, ...*
  - **表头行号：`2`**
  - **列名（逐字）：** `Function`、`Category`、`Subcategory`、`NIST SP 800-53, Revision 5 Control`
  - 数据从第 3 行起。Subcategory 单元格形如 `ID.AM-1: Physical devices and systems within the organization are inventoried`（CSF 1.1 连字符编号，控制号可逗号并列）

---

## 5. ISO 对照（只记结构，**不**做夹具）

原计划 URL `.../sp800-53r5-to-iso-27001-mapping.docx`：`CSRC/media/...` 301 到 `files/pubs/...` 后 **404**。

现行 NIST 署名文件：OLIR #155 xlsx（developer=NIST，Public Sector）。本地：`vendor/nist/sp800-53r5-to-iso-27001-mapping.xlsx`。

- sheets：`Relationships-AC`、`AT`、`AU`、`CA`、`CM`、`CP`、`IA`、`IR`、`MA`、`MP`、`PE`、`PL`、`PM`、`PS`、`PT`、`RA`、`SA`、`SC`、`SI`、`SR`、`Definitions`
- 映射表 **表头行号：`1`**
- **列名（逐字，含换行；以 `Relationships-AC` 为准）：**

| 列 | 精确字符串（Python `repr`） |
|---|---|
| A | `'Focal Document\nElement'` |
| B | `'Focal Document Element Description'` |
| C | `'Security Control Baseline'` |
| D | `'Reference Document Element'` |
| E | `'Reference Document\nElement Description\n(Optional)'` |
| F | `'Comments (optional)'` |
| G | `'Strength of Relationship (optional)'` |

列 A = 800-53 控制号（`AC-01`）；列 D = ISO/IEC 27001:2022 条款号（`A.5.1`、`5.2` 等编号，属事实性标识）。列 E 可能含 ISO 原文——**不要复制进夹具或 Git**。Task 9 解析 `vendor/` 中的 xlsx，不要按 `.docx` 表格假设。
