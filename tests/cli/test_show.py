"""`fr show` 要把解读渲出来，并且标明成色。主 spec §7.3.1（2026-08-22 自用降级）"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from framework_reader.cli.main import app
from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_interpretations,
)
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CONTROL = "NIST-CSF-2.0:DE.CM-01"


def _db(tmp_path: Path, state: str | None) -> Path:
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
    ])
    insert_controls(conn, [
        FrameworkControl(id=CONTROL, framework_id="NIST-CSF-2.0",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    if state is not None:
        prov = InterpretationProvenance()
        if state == "confirmed":
            prov = InterpretationProvenance(
                confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
            )
        insert_interpretations(conn, [Interpretation(
            control_id=CONTROL, state=InterpretationState(state),
            fields={
                name: Field(
                    value="防的是没人看网络" if name == "intent" else None,
                    basis=Basis.INFERRED,
                )
                for name in ALL_FIELDS
            },
            provenance=prov,
        )])
    conn.close()
    return path


def _run(db: Path):
    return CliRunner().invoke(app, ["show", CONTROL, "--db", str(db)])


def test_show_renders_the_interpretation(tmp_path):
    result = _run(_db(tmp_path, "draft"))
    assert result.exit_code == 0
    assert "防的是没人看网络" in result.stdout
    assert "What it defends against" in result.stdout


def test_a_draft_says_so(tmp_path):
    """草稿进包是为了能用，不是为了冒充定稿。成色必须写在脸上。"""
    assert "AI draft" in _run(_db(tmp_path, "draft")).stdout


def test_a_confirmed_interpretation_carries_no_warning(tmp_path):
    assert "AI draft" not in _run(_db(tmp_path, "confirmed")).stdout


def test_a_control_without_an_interpretation_still_shows(tmp_path):
    result = _run(_db(tmp_path, None))
    assert result.exit_code == 0
    assert CONTROL in result.stdout
    assert "AI draft" not in result.stdout


@pytest.mark.parametrize("state", ["draft", "confirmed", None])
def test_the_header_is_always_there(tmp_path, state):
    assert "Networks are monitored" in _run(_db(tmp_path, state)).stdout


# ---------- fr search ----------

def test_search_finds_a_control_by_chinese_text(tmp_path):
    db = _db(tmp_path, "draft")
    result = CliRunner().invoke(app, ["search", "没人看网络", "--db", str(db)])
    assert result.exit_code == 0
    assert CONTROL in result.stdout


def test_search_finds_a_control_by_short_id(tmp_path):
    db = _db(tmp_path, "draft")
    result = CliRunner().invoke(app, ["search", "DE.CM-01", "--db", str(db)])
    assert result.exit_code == 0
    assert CONTROL in result.stdout


def test_search_with_no_hits_says_so(tmp_path):
    db = _db(tmp_path, "draft")
    result = CliRunner().invoke(app, ["search", "量子计算", "--db", str(db)])
    assert result.exit_code == 1
    assert "No control found" in result.stdout
