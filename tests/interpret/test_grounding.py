"""受版权框架的接地材料。主 spec §4.1、§9

ISO 的条款原文不在我们手里，也不许进任何模型调用。能用的只有：
自写的中文标题 + 官方映射到的 800-53 控制（公共领域）。
"""
import json

import pytest

from framework_reader.interpret.grounding import (
    catalog_prose,
    grounding_lines,
    strip_oscal_params,
)

CATALOG = {
    "catalog": {
        "groups": [{
            "id": "ac",
            "controls": [
                {
                    "id": "ac-1", "title": "Policy and Procedures",
                    "parts": [
                        {"name": "statement", "parts": [
                            {"name": "item", "prose": "Develop and disseminate {{ insert: param, ac-1_prm_1 }} policy;"},
                            {"name": "item", "prose": "Review the policy annually."},
                        ]},
                        {"name": "guidance", "prose": "Access control policy addresses the AC family."},
                    ],
                },
                {"id": "ac-2", "title": "Account Management", "parts": [
                    {"name": "statement", "prose": "Define account types."}]},
            ],
        }],
    }
}


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "cat.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    return path


def test_oscal_parameter_placeholders_are_removed():
    """`{{ insert: param, ac-1_prm_1 }}` 直接发给模型只会污染输出。"""
    assert strip_oscal_params("Develop {{ insert: param, x }} policy") == "Develop policy"


def test_prose_is_flattened_from_nested_statement_items(catalog):
    text = catalog_prose(catalog)["AC-1"]
    assert "Develop and disseminate policy;" in text
    assert "Review the policy annually." in text


def test_guidance_is_included(catalog):
    assert "addresses the AC family" in catalog_prose(catalog)["AC-1"]


def test_a_control_without_nested_items_still_works(catalog):
    assert catalog_prose(catalog)["AC-2"].startswith("Define account types")


class _N:
    def __init__(self, cid, label):
        self.control_id, self.label = cid, label


class _API:
    def __init__(self, neighbors):
        self._n = neighbors

    def neighbors(self, control_id, exportable_only=False):
        return self._n


def test_lines_carry_the_id_the_title_and_the_prose(catalog):
    api = _API([_N("NIST-800-53-R5:AC-1", "Policy and Procedures")])
    lines = grounding_lines(api, "ISO-27002-2022:A.5.1", catalog_prose(catalog))
    assert lines[0].startswith("AC-1 Policy and Procedures: ")
    assert "Develop and disseminate policy" in lines[0]


def test_non_800_53_neighbors_are_ignored(catalog):
    """只有 800-53 是公共领域。别的框架的标题不能当接地材料发出去。"""
    api = _API([_N("ISO-27002-2022:A.8.16", "活动监控")])
    assert grounding_lines(api, "ISO-27002-2022:A.5.1", catalog_prose(catalog)) == []


def test_the_number_of_neighbors_is_capped(catalog):
    """有的条挂了 28 条边，全发出去既贵又稀释重点。"""
    api = _API([_N(f"NIST-800-53-R5:AC-{i}", f"标题 {i}") for i in range(1, 20)])
    assert len(grounding_lines(api, "x", catalog_prose(catalog), limit=6)) <= 6


def test_long_prose_is_truncated(catalog):
    prose = {"AC-1": "x" * 5000}
    api = _API([_N("NIST-800-53-R5:AC-1", "T")])
    line = grounding_lines(api, "x", prose, max_chars=200)[0]
    assert len(line) < 300
    assert line.rstrip().endswith("...")
