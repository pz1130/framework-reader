"""换版对照页：这个框架里谁能继承谁，一眼看完。

动作列只对「旧有解读、新没有」的行出继承表单——其余行渲染成说明文字，
后端校验是底线，前端不渲染是体面。
"""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.interpret.user_store import UserInterpretationStore
from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_supersessions,
)
from framework_reader.schema.entities import (
    Framework, FrameworkControl, LicenseTier, SupersedeRelation, Supersession,
)

OLD = "NIST-800-53-R5:AC-2.10"
NEW = "NIST-800-53-R5:AC-2"
FW = "NIST-800-53-R5"
PAGE = f"/f/{FW}/supersession"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    user_db = tmp_path / "user.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id=FW, name="SP 800-53 Rev.5", version="R5",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="ISO-27002-2022", name="ISO/IEC 27002:2022", version="2022",
                  tier=LicenseTier.C_PURCHASE, source_url="u", license_note="购买"),
    ])
    insert_controls(conn, [
        FrameworkControl(id=OLD, framework_id=FW,
                         label="Account management | Manual account changes",
                         label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=NEW, framework_id=FW,
                         label="Account management", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    insert_supersessions(conn, [Supersession(
        old_id=OLD, new_id=NEW,
        relation=SupersedeRelation.INCORPORATED_INTO)])
    conn.close()

    from framework_reader.web.app import create_app

    app = create_app(db, user_db=user_db)
    return TestClient(app, follow_redirects=False), user_db


def _draft(user_db, control_id):
    UserInterpretationStore(user_db).save(Interpretation(
        control_id=control_id,
        state=InterpretationState.DRAFT,
        fields={**{n: Field(value=None, basis=Basis.INFERRED)
                   for n in ALL_FIELDS},
                "intent": Field(value="写好的解读", basis=Basis.INFERRED)},
        provenance=InterpretationProvenance(),
    ))


def test_an_inheritable_row_shows_its_form(client):
    c, user_db = client
    _draft(user_db, OLD)
    page = c.get(PAGE).text
    assert OLD in page and NEW in page
    assert "Account management | Manual account changes" in page
    assert f'action="/c/{OLD}/inherit"' in page
    assert f'name="target" value="{NEW}"' in page


def test_a_row_without_an_old_interpretation_offers_nothing(client):
    c, _ = client
    page = c.get(PAGE).text
    assert "/inherit" not in page
    assert "has no interpretation" in page


def test_a_taken_new_control_says_so_instead_of_a_form(client):
    c, user_db = client
    _draft(user_db, OLD)
    _draft(user_db, NEW)
    page = c.get(PAGE).text
    assert "/inherit" not in page
    assert "already has an interpretation" in page


def test_the_page_tells_what_inheritance_does(client):
    c, _ = client
    page = c.get(PAGE).text
    assert "sign-off" in page, "页顶必须说清签字不带过去、新条款要重新确认"


def test_a_framework_without_edges_gets_a_quiet_page(client):
    c, _ = client
    page = c.get("/f/ISO-27002-2022/supersession").text
    assert "no supersession relationships" in page


def test_an_unknown_framework_is_a_404(client):
    c, _ = client
    assert c.get("/f/NOPE/supersession").status_code == 404


def test_the_framework_page_links_here(client):
    c, _ = client
    assert PAGE in c.get(f"/f/{FW}").text, "对照页入口应挂在框架页上"
