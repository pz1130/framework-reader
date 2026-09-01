"""起草时带上本组织自己的制度节选。见网页服务化设计 §8 S5

起草器写出来的是**通用**的落地建议。这个团队真正的落地方式写在他们自己的
制度里——「日志留存六个月」还是「一年」，是他们文件里的一行。
"""
import json

from framework_reader.interpret.drafter import draft_full_fields
from framework_reader.llm.client import LLMClient

PAYLOAD = {
    "intent": "防的是查不到", "plain_zh": "把日志留住",
    "practice": {"1": "有日志", "2": "集中存", "3": "有复核"},
    "evidence": "日志平台截图", "common_myth": None,
    "auditor_asks": None, "regional_note": None,
}

OURS = ["《安全管理制度》第一章 日志管理：安全日志留存不少于六个月。"]


class _Recorder(LLMClient):
    def __init__(self):
        self.user = ""

    def complete(self, system, messages, *, model, **kw):
        self.user = messages[0].content
        return json.dumps(PAYLOAD, ensure_ascii=False)


def _draft(**kw):
    client = _Recorder()
    draft_full_fields(client, control_id="ACME-1:4.1", model="m",
                      failure_dir=None, **kw)
    return client


def test_our_own_policy_reaches_the_model(): 
    client = _draft(outcome="日志要留存", neighbors=[], practice=OURS)
    assert "六个月" in client.user


def test_it_is_labelled_as_ours_not_as_the_standard(): 
    """混在一起的话，模型会把「我们已经这么做了」写成「标准要求这么做」。"""
    client = _draft(outcome="日志要留存", neighbors=[], practice=OURS)
    assert "Excerpts from this organization" in client.user


def test_the_model_is_told_to_use_it_rather_than_invent(): 
    client = _draft(outcome="日志要留存", neighbors=[], practice=OURS)
    assert "Never invent" in client.user


def test_a_copyrighted_framework_also_gets_our_own_policy():
    """原文受版权不给，但**我们自己的制度是我们自己的**，照样能给。"""
    client = _draft(outcome="", label="日志留存", neighbors=[],
                    grounding=["AU-11 Audit Record Retention：Retain records"],
                    practice=OURS)
    assert "六个月" in client.user
    assert "copyrighted" in client.user


def test_no_documents_means_no_such_section(): 
    client = _draft(outcome="日志要留存", neighbors=[], practice=[])
    assert "Excerpts from this organization" not in client.user


def test_nothing_changes_for_callers_that_do_not_pass_it():
    client = _draft(outcome="日志要留存", neighbors=[])
    assert "Excerpts from this organization" not in client.user
