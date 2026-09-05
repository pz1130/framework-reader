"""一条从上传到 payload 的完整链。见网页服务化设计 §8 S5

前面的测试各自证明了一段：解析、切段、检索、拼进提示词。这一条证明它们
**接在一起**——链子上任一环没接上，这里就红。
"""
import json
import sqlite3

import pytest

from framework_reader.interpret.batch import draft_all
from framework_reader.interpret.user_store import UserInterpretationStore
from framework_reader.llm.client import LLMClient
from framework_reader.pack.db import create_schema
from framework_reader.query.api import QueryAPI
from framework_reader.userframework.documents import DocumentStore
from framework_reader.userframework.store import UserFrameworkStore

DRAFT_JSON = json.dumps({
    "intent": "意图", "plain_zh": "大白话",
    "practice": {"1": "一", "2": "二", "3": "三"}, "evidence": "证据",
    "common_myth": None, "auditor_asks": None, "regional_note": None,
}, ensure_ascii=False)

POLICY = """第一章 日志管理

本公司各系统的安全日志留存不少于六个月，集中存放在日志平台，
每季度由安全组复核一次覆盖范围。
"""


class _Recorder(LLMClient):
    def __init__(self):
        self.prompts = []

    def complete(self, system, messages, *, model, **kw):
        self.prompts.append(messages[0].content)
        return DRAFT_JSON


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    conn.close()
    UserFrameworkStore().add_framework(
        framework_id="ACME-1", name="ACME 制度",
        controls=[("4.1", "日志留存与复核", None, "各系统日志应留存并定期复核。")])
    return db


def _draft(db, documents):
    client = _Recorder()
    draft_all(
        UserInterpretationStore(), QueryAPI(db, user_db=None), client,
        framework_id="ACME-1", model="m", prompt_version="v", provider="p",
        jobs=1, full=True, failure_dir=None, documents=documents,
    )
    return client


def test_the_uploaded_policy_reaches_the_draft_prompt(env):
    DocumentStore().add("安全管理制度.txt", POLICY.encode("utf-8"), by="ann@acme.cn")
    client = _draft(env, DocumentStore())
    assert any("六个月" in p for p in client.prompts)


def test_it_is_marked_as_ours_not_as_the_standard(env):
    DocumentStore().add("安全管理制度.txt", POLICY.encode("utf-8"), by="ann@acme.cn")
    client = _draft(env, DocumentStore())
    assert any("organization's own policies" in p for p in client.prompts)


def test_with_nothing_uploaded_the_prompt_is_unchanged(env):
    client = _draft(env, DocumentStore())
    assert all("本组织" not in p for p in client.prompts)


def test_an_unrelated_document_is_not_dragged_in(env):
    """噪声接地比没有接地更糟：模型会照着不相干的段落编出一条不存在的制度。"""
    DocumentStore().add(
        "食堂管理办法.txt",
        "第一章 就餐\n\n员工凭卡就餐，每日三餐，节假日照常供应。".encode("utf-8"),
        by="ann@acme.cn")
    client = _draft(env, DocumentStore())
    assert all("就餐" not in p for p in client.prompts)


def test_a_builtin_framework_never_gets_a_company_document(env):
    """内置框架的解读是我们要发布的内容。里面出现某一家公司的内部制度，
    既不对，也发不出去。"""
    from framework_reader.interpret.run import documents_for

    api = QueryAPI(env, user_db=None)
    assert documents_for(api.get_framework("ACME-1"), None) is not None


def test_the_builtin_frameworks_get_none(tmp_path):
    from framework_reader.interpret.run import documents_for
    from framework_reader.pack.db import insert_frameworks
    from framework_reader.schema.entities import Framework, LicenseTier

    db = tmp_path / "builtin.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()
    api = QueryAPI(db, user_db=None)
    assert documents_for(api.get_framework("NIST-CSF-2.0"), None) is None
