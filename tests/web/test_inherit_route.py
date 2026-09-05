"""继承动作落库留痕，条款页标出取代与继承来源。

审计只记「发生了什么」：事件 interpretation.inherit，detail 是
"{old} -> {new}"——与改字段的三种留痕同一套规矩，不记正文。
"""
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from framework_reader.identity.store import IdentityStore
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
URL = f"/c/{OLD}/inherit"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    user_db = tmp_path / "user.sqlite"
    identity_db = tmp_path / "identity.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=FW, name="SP 800-53 Rev.5", version="R5",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
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

    def _draft():
        UserInterpretationStore(user_db).save(Interpretation(
            control_id=OLD,
            state=InterpretationState.CONFIRMED,
            fields={**{n: Field(value=None, basis=Basis.INFERRED)
                       for n in ALL_FIELDS},
                    "intent": Field(value="写好的解读", basis=Basis.PRACTITIONER)},
            provenance=InterpretationProvenance(
                confirmed_by="jc",
                confirmed_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
        ))

    from framework_reader.web.app import create_app

    app = create_app(db, user_db=user_db, identity_db=identity_db)
    client = TestClient(app, follow_redirects=False)
    return {"client": client, "user_db": user_db,
            "identity_db": identity_db, "draft": _draft}


def test_inherit_lands_on_the_new_control_and_redirects_there(env):
    env["draft"]()
    result = env["client"].post(URL, data={"target": NEW})
    assert result.status_code == 303
    assert result.headers["location"] == f"/c/{NEW}"
    got = UserInterpretationStore(env["user_db"]).load(NEW)
    assert got.state is InterpretationState.DRAFT
    assert got.provenance.inherited_from == OLD
    assert got.fields["intent"].value == "写好的解读"


def test_the_old_interpretation_survives_the_inheritance(env):
    env["draft"]()
    env["client"].post(URL, data={"target": NEW})
    got = UserInterpretationStore(env["user_db"]).load(OLD)
    assert got.state is InterpretationState.CONFIRMED
    assert got.provenance.confirmed_by == "jc"


def test_the_action_is_logged_with_both_endpoints(env):
    env["draft"]()
    env["client"].post(URL, data={"target": NEW})
    events = IdentityStore(env["identity_db"]).audit(limit=50)
    inherit_events = [e for e in events if e["event"] == "interpretation.inherit"]
    assert len(inherit_events) == 1
    assert inherit_events[0]["detail"] == f"{OLD} -> {NEW}"


def test_a_taken_target_is_refused_in_chinese(env):
    env["draft"]()
    UserInterpretationStore(env["user_db"]).save(Interpretation(
        control_id=NEW, state=InterpretationState.DRAFT,
        fields={n: Field(value=None, basis=Basis.INFERRED) for n in ALL_FIELDS},
    ))
    result = env["client"].post(URL, data={"target": NEW})
    assert result.status_code == 409
    assert "already has an interpretation" in result.text


def test_a_missing_target_form_field_is_a_400(env):
    env["draft"]()
    result = env["client"].post(URL, data={})
    assert result.status_code == 400


def test_the_new_control_page_marks_its_inheritance(env):
    env["draft"]()
    env["client"].post(URL, data={"target": NEW})
    page = env["client"].get(f"/c/{NEW}").text
    assert "Inherited from" in page and OLD in page
    assert "re-confirmed" in page


def test_the_superseded_old_control_says_where_it_went(env):
    page = env["client"].get(f"/c/{OLD}").text
    assert "has been superseded" in page
    assert f'href="/c/{NEW}"' in page
    assert "/supersession" in page, "要有去对照页的出口"
