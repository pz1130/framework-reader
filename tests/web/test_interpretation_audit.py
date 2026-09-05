"""改解读要留痕。

签字确认一直进审计，但**改解读的三种方式一条都不留痕**：手动改字段、
AI 重写字段、AI 起草整条。对一个合规产品，「这句话是谁改的、什么时候改的」
现在答不出来——而解读本身只存了「谁签的字」，没有任何改动历史兜着。

**只记发生了什么，不记正文。** 审计日志是只追加的，把制度正文灌进去
就等于给它做了一个永久副本：删不掉，导出审计日志时一并出去。
"""
import re
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_frameworks
from framework_reader.schema.entities import Framework, LicenseTier

CID = "ACME-1:3.1"


def _make(tmp_path, monkeypatch, rewrite_runner=None):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    conn.close()

    from framework_reader.web.app import create_app

    made = TestClient(create_app(db, rewrite_runner=rewrite_runner),
                      follow_redirects=False)
    made.post("/import",
              data={"framework_id": "ACME-1", "name": "ACME 制度"},
              files={"file": ("f.csv", BytesIO(
                  "编号,标题,正文\n3.1,账号管理,应当为每人分配唯一账号。\n".encode()),
                  "text/csv")})
    return made


@pytest.fixture
def client(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _events(kind: str | None = None):
    entries = IdentityStore().audit(40)
    return [e for e in entries if kind is None or e["event"] == kind]


def test_editing_a_field_by_hand_lands_in_the_audit_log(client):
    client.post(f"/c/{CID}/edit/intent", data={"value": "防的是账号共用"})
    assert _events("interpretation.edit")


def test_the_line_says_which_control_and_which_field(client):
    client.post(f"/c/{CID}/edit/intent", data={"value": "防的是账号共用"})
    detail = _events("interpretation.edit")[0]["detail"]
    assert CID in detail
    assert "intent" in detail or "What it defends against" in detail


def test_the_line_says_how_much_changed_not_what(client):
    """长度说明「动过」，正文本身不进只追加的日志。"""
    client.post(f"/c/{CID}/edit/intent", data={"value": "防的是账号共用的风险"})
    detail = _events("interpretation.edit")[0]["detail"]
    assert "防的是账号共用" not in detail
    assert re.search(r"\d+\s*chars", detail)


def test_clearing_a_field_is_audited_too(client):
    """清空一个字段是最该留痕的那种改动——它把内容抹掉了。"""
    client.post(f"/c/{CID}/edit/intent", data={"value": "先写一句"})
    client.post(f"/c/{CID}/edit/intent", data={"value": ""})
    assert len(_events("interpretation.edit")) == 2
    assert "cleared" in _events("interpretation.edit")[0]["detail"]


def test_an_ai_rewrite_is_audited_as_ai_not_as_a_human_edit(tmp_path, monkeypatch):
    """谁写的要能分出来——这是这个产品的地基。"""
    client = _make(tmp_path, monkeypatch,
                   rewrite_runner=lambda *a, **k: "模型改过的话")
    client.post(f"/c/{CID}/rewrite/intent", data={"instruction": "再具体点"})
    assert _events("interpretation.rewrite")
    assert not _events("interpretation.edit")


def test_the_rewrite_line_keeps_the_instruction_out_of_the_log(tmp_path,
                                                               monkeypatch):
    """要求是你打的字，可能带公司内部信息。日志里只说「提了要求」。"""
    client = _make(tmp_path, monkeypatch,
                   rewrite_runner=lambda *a, **k: "模型改过的话")
    client.post(f"/c/{CID}/rewrite/intent",
                data={"instruction": "我们用的是内部系统 XYZ-9"})
    detail = _events("interpretation.rewrite")[0]["detail"]
    assert "XYZ-9" not in detail


def test_a_failed_action_is_not_audited_as_a_change(client):
    """没改成的事不该在日志里长得像改成了。"""
    client.post(f"/c/{CID}/edit/nosuchfield", data={"value": "x"})
    assert _events("interpretation.edit") == []


def test_asking_the_ai_to_draft_a_control_is_audited(tmp_path, monkeypatch):
    """起草会一次写进七个字段。不留痕的话，一整条解读凭空出现而没人知道
    是谁按的按钮。"""
    from framework_reader.web import jobs

    jobs.reset()
    client = _make(tmp_path, monkeypatch)
    client.post(f"/c/{CID}/draft")
    assert _events("interpretation.draft")
    jobs.reset()


def test_the_draft_line_names_the_control(tmp_path, monkeypatch):
    from framework_reader.web import jobs

    jobs.reset()
    client = _make(tmp_path, monkeypatch)
    client.post(f"/c/{CID}/draft")
    assert CID in _events("interpretation.draft")[0]["detail"]
    jobs.reset()


def test_a_draft_that_the_budget_refused_is_not_audited(tmp_path, monkeypatch):
    """没跑成的事不该在日志里长得像跑成了。"""
    from framework_reader.llm.config import ModelConfig
    from framework_reader.web import jobs

    jobs.reset()
    client = _make(tmp_path, monkeypatch)
    ModelConfig().set_limits(draft_cap_month=1, by="x")
    client.post(f"/c/{CID}/draft")          # 用掉唯一那一格
    before = len(_events("interpretation.draft"))
    client.post(f"/c/{CID}/draft")          # 这次被拒
    assert len(_events("interpretation.draft")) == before
    jobs.reset()
