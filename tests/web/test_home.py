"""首页是搜索工作台：搜框、常搜条款、每天三条。框架挪到「框架」标签。"""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

FW = "NIST-CSF-2.0"
CONTROLS = [
    ("DE.CM-01", "Networks are monitored", "防的是没人看网络"),
    ("DE.CM-02", "Physical environment is monitored", "防的是机房没人看"),
    ("PR.AA-01", "Identities and credentials are managed", "防的是账号乱发"),
    ("PR.AA-02", "Identities are proofed", "防的是假身份进门"),
]


def _interp(control_id: str, intent: str) -> Interpretation:
    return Interpretation(
        control_id=control_id, state=InterpretationState.DRAFT,
        fields={
            name: Field(
                value={"1": "a", "2": "b", "3": "c"} if name == "practice"
                else (intent if name == "intent"
                      else ("人话：" + intent if name == "plain_zh" else "x")),
                basis=Basis.INFERRED)
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=FW, name="NIST Cybersecurity Framework 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [
        FrameworkControl(
            id=f"{FW}:{short}", framework_id=FW, label=label,
            label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)
        for short, label, _intent in CONTROLS
    ])
    insert_interpretations(conn, [
        _interp(f"{FW}:{short}", intent) for short, _label, intent in CONTROLS
    ])
    conn.close()
    from framework_reader.web.app import create_app
    return TestClient(create_app(db), follow_redirects=False)


def test_the_home_page_is_a_search_desk_not_a_redirect(client):
    result = client.get("/")
    assert result.status_code == 200
    page = result.text
    assert 'action="/search"' in page
    assert 'name="q"' in page
    assert "Built-in frameworks" not in page
    assert 'href="/f/NIST-CSF-2.0"' not in page


def test_the_top_bar_has_a_frameworks_tab(client):
    for path in ("/", "/frameworks", f"/f/{FW}"):
        page = client.get(path).text
        assert 'href="/frameworks"' in page, path
        assert ">Frameworks<" in page, path


def test_the_frameworks_tab_still_lists_builtins_and_imports(client):
    page = client.get("/frameworks").text
    assert "NIST Cybersecurity Framework 2.0" in page
    assert "Built-in" in page
    assert "Import your own framework" not in page


def test_the_frameworks_page_does_not_carry_the_search_box(client):
    """搜框只在首页。框架页是目录，不再兼做搜索入口。"""
    assert 'action="/search"' not in client.get("/frameworks").text


def test_an_empty_search_returns_to_the_home_desk(client):
    result = client.get("/search")
    assert result.status_code == 303
    assert result.headers["location"] == "/"


def test_home_shows_three_daily_controls_to_study(client):
    page = client.get("/").text
    assert "Learn three today" in page
    linked = [short for short, _label, _ in CONTROLS if short in page]
    # 四条里抽出三条，恰好这个数，且都点得进条款页。
    assert len(linked) == 3
    for short in linked:
        assert f'href="/c/{FW}:{short}"' in page


def test_the_daily_three_are_stable_across_a_day(client):
    first = client.get("/").text
    second = client.get("/").text
    ids = [short for short, *_ in CONTROLS]
    assert [s for s in ids if s in first] == [s for s in ids if s in second]


def test_daily_cards_show_a_learning_snippet(client):
    """快速学习要看见这句话在防什么，不能只丢一个英文标题。"""
    page = client.get("/").text
    # 四条 intent 里至少三条会出现一条（抽中的那三张）。
    snippets = [intent for _s, _l, intent in CONTROLS if intent in page]
    assert len(snippets) == 3


def test_daily_cards_name_their_framework(client):
    daily = client.get("/").text.split("Learn three today", 1)[1]
    assert "NIST Cybersecurity Framework 2.0" in daily


def test_popular_is_quiet_before_anyone_has_searched(client):
    page = client.get("/").text
    assert "Frequently searched" in page
    assert "No search history" in page


def test_the_daily_three_have_a_refresh_button(client):
    page = client.get("/").text
    assert "Shuffle" in page
    # 下一批的种子写在表单里：默认页（roll=0）的下一批是 roll=1。
    assert 'name="roll"' in page and 'value="1"' in page


def test_a_rolled_batch_is_stable_within_the_day(client):
    first = client.get("/", params={"roll": 4}).text
    second = client.get("/", params={"roll": 4}).text
    ids = [short for short, *_ in CONTROLS]
    assert [s for s in ids if s in first] == [s for s in ids if s in second]


def test_a_rolled_batch_still_carries_three_cards(client):
    page = client.get("/", params={"roll": 4}).text
    linked = [short for short, *_ in CONTROLS if f'href="/c/{FW}:{short}"' in page]
    assert len(linked) == 3


def test_the_refresh_button_advances_the_roll(client):
    page = client.get("/", params={"roll": 2}).text
    assert 'value="3"' in page


def test_a_searched_control_shows_up_in_popular(client):
    client.get("/search", params={"q": "DE.CM-01"})
    page = client.get("/").text
    assert "No search history" not in page
    assert "DE.CM-01" in page
    assert f'href="/c/{FW}:DE.CM-01"' in page


def test_more_searches_rank_a_control_higher(client):
    client.get("/search", params={"q": "DE.CM-01"})
    client.get("/search", params={"q": "PR.AA-01"})
    client.get("/search", params={"q": "PR.AA-01"})
    page = client.get("/").text
    popular = page.split("Frequently searched", 1)[1].split("Learn three today", 1)[0]
    assert popular.find("PR.AA-01") < popular.find("DE.CM-01")
