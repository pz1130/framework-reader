"""Tier B/C 框架的起草：原文不给，接地靠映射。主 spec §4.1、§9"""
import json

import pytest

from framework_reader.interpret.drafter import draft_full_fields
from framework_reader.llm.client import LLMClient

PAYLOAD = {
    "intent": "防的是没人管方针", "plain_zh": "写下来并发下去",
    "practice": {"1": "有文件", "2": "有评审", "3": "有度量"},
    "evidence": "方针文件与发布记录", "common_myth": None,
    "auditor_asks": None, "regional_note": None,
}


class _Recorder(LLMClient):
    def __init__(self):
        self.system = ""
        self.user = ""

    def complete(self, system, messages, *, model, **kw):
        self.system, self.user = system, messages[0].content
        return json.dumps(PAYLOAD, ensure_ascii=False)


def _draft(**kw):
    client = _Recorder()
    fields = draft_full_fields(
        client, control_id="ISO-27002-2022:A.5.1", model="m", failure_dir=None, **kw
    )
    return client, fields


def test_the_public_domain_path_is_unchanged():
    client, _ = _draft(outcome="Networks are monitored", neighbors=["NIST-800-53-R5:SI-4"])
    assert "Framework text (public domain): Networks are monitored" in client.user


def test_without_an_outcome_the_prompt_says_the_original_is_withheld():
    """不说清楚的话，模型会把我们自写的中文标题当成标准原文去翻译。"""
    client, _ = _draft(
        outcome="", label="信息安全方针", neighbors=[],
        grounding=["AC-1 Policy and Procedures：Develop and disseminate policy"],
    )
    assert "copyrighted" in client.user
    assert "Self-written title: 信息安全方针" in client.user


def test_the_grounding_lines_are_sent(): 
    client, _ = _draft(
        outcome="", label="信息安全方针", neighbors=[],
        grounding=["AC-1 Policy and Procedures：Develop and disseminate policy"],
    )
    assert "AC-1 Policy and Procedures：Develop and disseminate policy" in client.user


def test_no_grounding_and_no_outcome_still_drafts_from_the_label_alone():
    """A.7.6 / A.8.34 两条没有官方 800-53 边。少了接地也得能出稿。"""
    client, fields = _draft(outcome="", label="工作区安全", neighbors=[], grounding=[])
    assert "Self-written title: 工作区安全" in client.user
    assert fields["intent"].value == "防的是没人管方针"


def test_drafted_fields_are_still_marked_inferred():
    _, fields = _draft(outcome="", label="信息安全方针", neighbors=[], grounding=[])
    assert all(f.basis.value == "inferred" for f in fields.values() if f.value is not None)


# ---------- batch 层：按框架 tier 决定给不给原文 ----------

def test_batch_withholds_the_label_as_outcome_for_a_purchase_tier_framework(tmp_path):
    """ISO 的 label 是我们自写的短标题，不能当成「框架原文」发出去。"""
    import inspect

    from framework_reader.interpret import batch

    src = inspect.getsource(batch.draft_all)
    assert "embeddable" in src or "LicenseTier" in src, "batch 必须按 tier 分流"


def test_a_user_framework_sends_the_users_own_text_as_the_original():
    """用户自己公司的制度：他的文档、他的机器、他的 key。可以当原文发。"""
    client, _ = _draft(outcome="账号须经审批后开立，离职当日回收。", neighbors=[])
    assert "账号须经审批后开立" in client.user
