"""fr assess / fr gap。主 spec §7.3.3"""
import sqlite3

import pytest
from typer.testing import CliRunner

from framework_reader.cli.main import app
from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance, InterpretationState,
)
from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

IDS = ["NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-02", "NIST-CSF-2.0:GV.OC-01"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [
        FrameworkControl(id=cid, framework_id="NIST-CSF-2.0", label=f"标题 {cid[-2:]}",
                         label_is_original=True, framework_tier=LicenseTier.A_EMBEDDABLE)
        for cid in IDS
    ])
    insert_interpretations(conn, [Interpretation(
        control_id=IDS[0], state=InterpretationState.DRAFT,
        fields={
            name: Field(
                value={"1": "有基础监控", "2": "覆盖面有清单", "3": "自动化"}
                if name == "practice" else ("看板截图" if name == "evidence" else "x"),
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance(),
    )])
    conn.close()
    return path


def _run(args, db, stdin=""):
    return CliRunner().invoke(app, args + ["--db", str(db)], input=stdin)


def test_assess_records_a_level_and_a_note(db):
    result = _run(["assess", IDS[0]], db, stdin="1\n只有边界有探针\n")
    assert result.exit_code == 0, result.output
    gap = _run(["gap"], db)
    assert "只有边界有探针" in gap.stdout
    assert "覆盖面有清单" in gap.stdout


def test_assess_shows_the_three_rungs_before_asking(db):
    out = _run(["assess", IDS[0]], db, stdin="1\n\n").stdout
    for rung in ("有基础监控", "覆盖面有清单", "自动化"):
        assert rung in out


def test_a_function_filter_only_walks_that_function(db):
    out = _run(["assess", "--function", "GV"], db, stdin="0\n\n").stdout
    assert "GV.OC-01" in out
    assert "DE.CM-01" not in out


def test_gap_denominator_is_the_framework_not_what_was_assessed(db):
    _run(["assess", IDS[0]], db, stdin="1\n\n")
    assert "1/3 controls" in _run(["gap"], db).stdout


def test_gap_before_any_assessment_says_so(db):
    assert "No self-assessment yet" in _run(["gap"], db).stdout


def test_assess_survives_a_content_pack_rebuild(db, tmp_path):
    """make clean 会 rm -rf build/。自评数据不在那儿，重建内容包不该丢。"""
    _run(["assess", IDS[0]], db, stdin="2\n补了主机侧\n")
    db.unlink()
    assert not db.exists()
    from framework_reader.assess.store import AssessStore

    assert AssessStore().get(IDS[0]).level == 2


def test_gap_can_be_written_to_a_file(db, tmp_path):
    _run(["assess", IDS[0]], db, stdin="1\n只有边界有探针\n")
    out = tmp_path / "gap.md"
    result = _run(["gap", "--out", str(out)], db)
    assert result.exit_code == 0
    assert "只有边界有探针" in out.read_text(encoding="utf-8")


# ---------- SoA 模式（无解读的框架） ----------

@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="ISO-27002-2022", name="ISO/IEC 27002:2022", version="2022",
        tier=LicenseTier.C_PURCHASE, source_url="u", license_note="购买")])
    insert_controls(conn, [
        FrameworkControl(id="ISO-27002-2022:A.5.1", framework_id="ISO-27002-2022",
                         label="信息安全方针", label_is_original=False,
                         framework_tier=LicenseTier.C_PURCHASE),
        FrameworkControl(id="ISO-27002-2022:A.7.4", framework_id="ISO-27002-2022",
                         label="物理安全监控", label_is_original=False,
                         framework_tier=LicenseTier.C_PURCHASE),
    ])
    conn.close()
    return path


def test_a_framework_without_interpretations_asks_soa_questions(iso_db):
    """没有 practice 三档就问不出「几档」。该问的是：适用吗、做了吗、证据在哪。"""
    out = _run(["assess", "--framework", "ISO-27002-2022"], iso_db, stdin="2\n见 SEC-POL-001\n").stdout
    assert "Implementation status" in out
    assert "What level" not in out


def test_soa_status_is_stored_as_words_not_digits(iso_db):
    _run(["assess", "ISO-27002-2022:A.5.1", "--framework", "ISO-27002-2022"],
         iso_db, stdin="2\n见 SEC-POL-001\n")
    from framework_reader.assess.store import AssessStore

    got = AssessStore().get("ISO-27002-2022:A.5.1")
    assert got.status == "implemented"
    assert got.level is None


def test_soa_export_lists_every_control_including_unfilled(iso_db, tmp_path):
    _run(["assess", "ISO-27002-2022:A.5.1", "--framework", "ISO-27002-2022"],
         iso_db, stdin="2\n见 SEC-POL-001\n")
    out = tmp_path / "soa.md"
    result = _run(["soa", "--framework", "ISO-27002-2022", "--out", str(out)], iso_db)
    assert result.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "A.5.1" in text and "A.7.4" in text
    assert "TBD" in text


def test_soa_csv_is_written_when_the_extension_says_so(iso_db, tmp_path):
    out = tmp_path / "soa.csv"
    _run(["soa", "--framework", "ISO-27002-2022", "--out", str(out)], iso_db)
    assert out.read_text(encoding="utf-8").startswith("Control,")


# ---------- 93 条要分几次做完 ----------

def test_the_filter_matches_an_iso_theme_not_just_a_csf_function(iso_db):
    """A.5.1 按点切出来是「A」，93 条全中——ISO 需要的是前缀匹配。"""
    out = _run(["assess", "--framework", "ISO-27002-2022", "--function", "A.7"],
               iso_db, stdin="\n").stdout
    assert "A.7.4" in out
    assert "A.5.1" not in out


def test_progress_is_shown_so_a_long_session_is_survivable(iso_db):
    out = _run(["assess", "--framework", "ISO-27002-2022"], iso_db, stdin="\n\n").stdout
    assert "[1/2]" in out


def test_already_answered_controls_do_not_come_back(iso_db):
    _run(["assess", "ISO-27002-2022:A.5.1", "--framework", "ISO-27002-2022"],
         iso_db, stdin="2\n\n")
    out = _run(["assess", "--framework", "ISO-27002-2022"], iso_db, stdin="\n").stdout
    assert "A.5.1" not in out
    assert "A.7.4" in out


def test_the_tail_says_how_many_are_left(iso_db):
    out = _run(["assess", "ISO-27002-2022:A.5.1", "--framework", "ISO-27002-2022"],
               iso_db, stdin="2\n\n").stdout
    assert "Remaining" in out


def test_third_party_implemented_is_its_own_status(iso_db):
    """云厂商/业主实施的控制**是适用的**。标成「不适用」是审核员专挑的错。"""
    _run(["assess", "ISO-27002-2022:A.7.4", "--framework", "ISO-27002-2022"],
         iso_db, stdin="3\n机房由 AWS 运营，见其 SOC 2 Type II 报告\n")
    from framework_reader.assess.store import AssessStore

    got = AssessStore().get("ISO-27002-2022:A.7.4")
    assert got.status == "implemented by a third party"
    assert got.applicable is True


def test_the_prompt_offers_the_third_party_option(iso_db):
    out = _run(["assess", "--framework", "ISO-27002-2022"], iso_db, stdin="\n\n").stdout
    assert "3=by a third party" in out
