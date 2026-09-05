"""整改台账的路由。差距报告说「下一步做什么」，台账记「谁、什么时候、做了没有」。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from framework_reader.assess.remediation import RemediationStore
from framework_reader.assess.store import AssessStore
from framework_reader.identity.store import IdentityStore
from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

FW = "NIST-CSF-2.0"
CID = f"{FW}:DE.CM-01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id=FW, name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [
        FrameworkControl(
            id=CID, framework_id=FW, label="Networks are monitored",
            label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    conn.close()
    from framework_reader.web.app import create_app

    identity = IdentityStore()
    identity.create_account(email="boss@acme.cn", password="pw-boss-boss",
                            roles=("admin",))
    identity.create_account(email="ann@acme.cn", password="pw-ann-ann",
                            roles=("author",))
    return type("Env", (), {"app": create_app(db), "identity": identity})()


def _client(env, email="ann@acme.cn", password="pw-ann-ann") -> TestClient:
    client = TestClient(env.app, follow_redirects=False)
    client.post("/login", data={"email": email, "password": password})
    return client


def _csrf(client) -> str:
    import re

    page = client.get("/frameworks").text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def _post(client, path, **data):
    return client.post(path, data={"csrf": _csrf(client), **data},
                       follow_redirects=False)


def test_the_ledger_is_empty_before_anyone_plans(env):
    page = _client(env).get(f"/f/{FW}/remediation").text
    assert "The ledger is empty" in page


def test_planning_the_gap_creates_one_row_per_gap_item(env):
    """差距报告 → 一键立项 → 台账上有条目。没人认领的整改不会自己发生。"""
    client = _client(env)
    AssessStore().record(CID, level=1, note="只记日志不告警")
    gap = client.get(f"/f/{FW}/gap").text
    assert "Track these 1 gaps in the ledger" in gap
    assert _post(client, f"/f/{FW}/remediation/plan").status_code == 303
    ledger = client.get(f"/f/{FW}/remediation").text
    assert "DE.CM-01" in ledger
    assert "To do" in ledger
    # 立项过一遍之后，报告上不再有可立项的数。
    assert "Track these" not in client.get(f"/f/{FW}/gap").text


def test_a_row_keeps_owner_and_due_through_an_update(env):
    client = _client(env)
    RemediationStore().start(CID)
    _post(client, f"/f/{FW}/remediation", control_id=CID,
          state="doing", owner="老张", due="2026-09-30", note="告警接进 SOC")
    ledger = client.get(f"/f/{FW}/remediation").text
    assert "老张" in ledger and "2026-09-30" in ledger
    assert "In progress" in ledger and "告警接进 SOC" in ledger


def test_planning_twice_does_not_wipe_what_the_human_filled(env):
    client = _client(env)
    AssessStore().record(CID, level=1)
    _post(client, f"/f/{FW}/remediation/plan")
    _post(client, f"/f/{FW}/remediation", control_id=CID, owner="老张")
    _post(client, f"/f/{FW}/remediation/plan")
    assert RemediationStore().get(CID).owner == "老张"


def test_add_by_short_ref(env):
    """台账补录的输入框里人填的是 4.1 这种短编号，不是完整 id。"""
    client = _client(env)
    _post(client, f"/f/{FW}/remediation", ref="DE.CM-01", owner="老李")
    assert RemediationStore().get(CID).owner == "老李"


def test_a_ref_that_is_not_in_this_framework_is_refused(env):
    client = _client(env)
    result = _post(client, f"/f/{FW}/remediation", ref="NOPE-9")
    assert result.status_code == 404
    assert RemediationStore().get(f"{FW}:NOPE-9") is None


def test_removing_a_row(env):
    client = _client(env)
    RemediationStore().start(CID)
    _post(client, f"/f/{FW}/remediation/remove", control_id=CID)
    assert RemediationStore().get(CID) is None


def test_a_reassessment_shows_up_as_a_change_on_the_gap_page(env):
    """复评对比：1 档 → 2 档要看得见「从多少到多少」。"""
    client = _client(env)
    store = AssessStore()
    store.record(CID, level=1)
    store.record(CID, level=2)
    gap = client.get(f"/f/{FW}/gap").text
    assert "Re-assessment comparison" in gap
    assert "L1" in gap and "L2" in gap


def test_the_ledger_shows_where_the_control_stands(env):
    """台账行要带上当前档位和下一档的原话——不用跳回差距报告。"""
    client = _client(env)
    AssessStore().record(CID, level=1)
    RemediationStore().start(CID)
    ledger = client.get(f"/f/{FW}/remediation").text
    assert "Now: Level 1" in ledger


def test_an_admin_cannot_fiddle_with_the_ledger(env):
    """管系统的不代表懂这条控制——跟自评同一个权限域。"""
    result = _post(_client(env, "boss@acme.cn", "pw-boss-boss"),
                   f"/f/{FW}/remediation/plan")
    assert result.status_code == 403
