"""每天三条：同一天稳定，换一天会换。"""
import sqlite3
from datetime import date

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.query.api import QueryAPI
from framework_reader.query.daily import daily_controls
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


def _db(path):
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="CSF", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    controls = []
    interps = []
    for i in range(6):
        cid = f"NIST-CSF-2.0:DE.CM-{i:02d}"
        controls.append(FrameworkControl(
            id=cid, framework_id="NIST-CSF-2.0",
            label=f"Control {i}", label_is_original=True,
            framework_tier=LicenseTier.A_EMBEDDABLE))
        interps.append(Interpretation(
            control_id=cid, state=InterpretationState.DRAFT,
            fields={
                name: Field(
                    value=f"intent-{i}" if name in ("intent", "plain_zh") else "x",
                    basis=Basis.INFERRED)
                for name in ALL_FIELDS
            },
            provenance=InterpretationProvenance()))
    insert_controls(conn, controls)
    insert_interpretations(conn, interps)
    conn.close()
    return QueryAPI(path)


def test_it_picks_three(tmp_path):
    picked = daily_controls(_db(tmp_path / "c.sqlite"), today=date(2026, 8, 29))
    assert len(picked) == 3
    assert len({p["id"] for p in picked}) == 3


def test_the_same_day_returns_the_same_three(tmp_path):
    api = _db(tmp_path / "c.sqlite")
    a = [p["id"] for p in daily_controls(api, today=date(2026, 8, 29))]
    b = [p["id"] for p in daily_controls(api, today=date(2026, 8, 29))]
    assert a == b


def test_a_different_day_can_pick_a_different_set(tmp_path):
    api = _db(tmp_path / "c.sqlite")
    a = {p["id"] for p in daily_controls(api, today=date(2026, 8, 29))}
    b = {p["id"] for p in daily_controls(api, today=date(2026, 8, 30))}
    assert a != b


def test_the_same_roll_returns_the_same_three(tmp_path):
    """「换一批」的批次也讲契约：同一个批次当天稳定，书签收得住。"""
    api = _db(tmp_path / "c.sqlite")
    a = [p["id"] for p in daily_controls(api, today=date(2026, 8, 29), roll=2)]
    b = [p["id"] for p in daily_controls(api, today=date(2026, 8, 29), roll=2)]
    assert a == b


def test_the_seed_mixes_in_the_roll_only_when_rolled():
    """roll=0 保持老的日期种子——「同一天同一组」的契约对默认视图不变。"""
    from framework_reader.query.daily import _seed

    assert _seed(date(2026, 8, 29), 0) == "2026-08-29"
    assert _seed(date(2026, 8, 29), 3) == "2026-08-29#3"
    assert _seed(date(2026, 8, 29), -1) == "2026-08-29"


def test_each_card_carries_a_snippet(tmp_path):
    picked = daily_controls(_db(tmp_path / "c.sqlite"), today=date(2026, 8, 29))
    for card in picked:
        assert card["snippet"].startswith("intent-")
        assert card["short"].startswith("DE.CM-")
        assert card["label"].startswith("Control ")


def test_each_card_names_its_framework(tmp_path):
    """条号 alone 看不出是 CSF 还是 800-53。框架名要写在卡片上。"""
    picked = daily_controls(_db(tmp_path / "c.sqlite"), today=date(2026, 8, 29))
    for card in picked:
        assert card["framework"] == "CSF"
