"""把 3 条手写黄金样例当 few-shot：让模型学颗粒度，不是抄内容。"""
import json

import pytest

from framework_reader.interpret.drafter import draft_full_fields
from framework_reader.interpret.golden import GOLDEN_CONTROLS, few_shot_examples
from framework_reader.llm.client import FakeClient

FULL = json.dumps({
    "intent": "i", "plain_zh": "p",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "e",
    "common_myth": "m", "auditor_asks": ["a"], "regional_note": None,
}, ensure_ascii=False)


def test_examples_come_from_the_handwritten_goldens():
    ids = [e.control_id for e in few_shot_examples()]
    assert set(ids) <= set(GOLDEN_CONTROLS)
    assert ids


def test_a_control_is_never_its_own_example():
    """拿 PR.AA-05 自己的手写版去生成 PR.AA-05，那不是学颗粒度，是抄答案。"""
    target = "NIST-CSF-2.0:PR.AA-05"
    assert target not in [e.control_id for e in few_shot_examples(exclude=target)]
    assert len(few_shot_examples(exclude=target)) == len(GOLDEN_CONTROLS) - 1


def test_examples_reach_the_system_prompt():
    client = FakeClient([FULL])
    draft_full_fields(
        client, control_id="NIST-CSF-2.0:DE.CM-01",
        outcome="Networks are monitored", neighbors=[], model="m",
        examples=few_shot_examples(exclude="NIST-CSF-2.0:DE.CM-01"),
    )
    system = client.calls[0]["system"]
    # 手写版里那句具体的追问，是这次 few-shot 要传递的颗粒度
    assert "抽三个离职或调岗账号" in system


def test_no_examples_still_works():
    client = FakeClient([FULL])
    draft_full_fields(
        client, control_id="X:1", outcome="o", neighbors=[], model="m", examples=None
    )
    assert client.calls[0]["system"]


def test_prompt_version_pins_the_golden_content():
    """few-shot 变了，提示词就变了——provenance 必须能区分。"""
    from framework_reader.prompts import full_drafter_version

    version = full_drafter_version()
    assert version.startswith("2026.08-f2+golden:")
    assert len(version.split(":")[-1]) == 8
    assert version == full_drafter_version()
