"""首页搜索：先字面（关键词 / 条款号），没命中才让 AI 扩词。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CID = "NIST-CSF-2.0:DE.CM-01"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST Cybersecurity Framework 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id=CID, framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    return path


def _client(db, search_runner=None):
    from framework_reader.web.app import create_app

    return TestClient(create_app(db, search_runner=search_runner),
                      follow_redirects=False)


def test_the_home_page_has_a_search_box(db):
    page = _client(db).get("/").text
    assert 'action="/search"' in page
    assert 'name="q"' in page


def test_a_control_id_search_lists_the_control(db):
    page = _client(db).get("/search", params={"q": "DE.CM-01"}).text
    assert "DE.CM-01" in page
    assert "Networks are monitored" in page
    assert f'href="/c/{CID}"' in page
    assert "literal" in page


def test_a_literal_hit_does_not_call_the_model(db):
    calls = []

    def runner(query):
        calls.append(query)
        return '{"terms": ["监控"], "ids": []}'

    _client(db, search_runner=runner).get("/search", params={"q": "DE.CM-01"})
    assert calls == []


def test_a_miss_asks_the_model_to_expand(db):
    calls = []

    def runner(query):
        calls.append(query)
        return '{"terms": ["日志留存"], "ids": ["DE.CM-01"]}'

    page = _client(db, search_runner=runner).get(
        "/search", params={"q": "服务器操作记录要留多久"}).text
    assert calls == ["服务器操作记录要留多久"]
    assert "DE.CM-01" in page
    assert "日志留存" in page
    assert "No literal hits" in page


def test_a_hallucinated_id_is_not_a_result(db):
    """模型编的号可以写在『扩了哪些词』里，但不能变成一条点得进去的结果。"""
    def runner(query):
        return '{"terms": [], "ids": ["PCI-DSS:1.1"]}'

    page = _client(db, search_runner=runner).get(
        "/search", params={"q": "支付卡数据怎么保护"}).text
    assert 'href="/c/PCI-DSS:1.1"' not in page
    assert "Expanded the query like this and still nothing" in page


def test_an_empty_query_goes_back_home(db):
    result = _client(db).get("/search")
    assert result.status_code == 303
    assert result.headers["location"] == "/"


def test_an_ai_search_is_charged(db):
    from framework_reader.llm.config import ModelConfig

    def runner(query):
        return '{"terms": [], "ids": ["DE.CM-01"]}'

    _client(db, search_runner=runner).get(
        "/search", params={"q": "服务器操作记录要留多久"})
    assert ModelConfig().spent_this_month() == 1


def test_a_literal_search_is_not_charged(db):
    from framework_reader.llm.config import ModelConfig

    _client(db).get("/search", params={"q": "DE.CM-01"})
    assert ModelConfig().spent_this_month() == 0


def test_a_viewer_does_not_spend_on_a_miss(db):
    """字面搜索谁都能用。相近语义要花钱，viewer 没有起草权。"""
    from framework_reader.identity.store import IdentityStore
    from framework_reader.llm.config import ModelConfig
    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="viewer@acme.cn", password="pw-role-role",
                            roles=("viewer",))
    calls = []

    def runner(query):
        calls.append(query)
        return '{"terms": [], "ids": ["DE.CM-01"]}'

    client = TestClient(create_app(db, search_runner=runner),
                        follow_redirects=False)
    client.post("/login", data={
        "email": "viewer@acme.cn", "password": "pw-role-role"})
    page = client.get("/search", params={"q": "量子计算"}).text
    assert calls == []
    assert "drafting" in page
    assert ModelConfig().spent_this_month() == 0
