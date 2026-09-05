"""自用日志。主 spec §7.3.1（2026-08-22 起是唯一的验证手段）"""
import json

import pytest

from framework_reader.usage import (
    QUERY_COMMANDS,
    Entry,
    load,
    record,
    render_report,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path))
    return tmp_path


def test_one_line_per_call(home):
    record("show", target="NIST-CSF-2.0:DE.CM-01")
    record("stats")
    lines = (home / "usage.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["command"] == "show"
    assert json.loads(lines[0])["target"] == "NIST-CSF-2.0:DE.CM-01"


def test_entries_round_trip(home):
    record("show", target="C1")
    entries = load()
    assert [e.command for e in entries] == ["show"]
    assert entries[0].at.tzinfo is not None


def test_logging_never_breaks_the_command(home, monkeypatch):
    """查一条控制不该因为日志写不进去而失败。"""
    monkeypatch.setenv("FRAMEWORK_READER_HOME", "/proc/nonexistent/nope")
    record("show", target="C1")   # 不抛就算过


def test_a_note_is_recorded_as_its_own_kind(home):
    record("usage", note="翻 CSF 的时候用了，省了查原文")
    entry = load()[0]
    assert entry.note == "翻 CSF 的时候用了，省了查原文"


# ---------- 查询 ≠ 生产 ----------

def test_building_the_tool_is_not_using_the_tool():
    """把 draft/interview 算成「使用」，是最舒服的一种自欺。"""
    assert QUERY_COMMANDS == {"show", "stats", "search", "frameworks"}


def test_search_is_using_the_tool_not_building_it():
    """`fr search` 是**主入口**——Skill 教的第一句就是「先搜，再看」，
    条号记不住是常态，真实的查询几乎都从它开始。

    它落在「生产」那一边的话，唯一的仪表会把你的每一次真实使用记成
    「你在开发」，「查询 0 次」永远是 0，而那个 0 什么也不说明。
    """
    assert "search" in QUERY_COMMANDS


def test_the_production_side_still_holds_the_ones_that_build_the_tool():
    for command in ("draft", "interview", "blindtest", "build", "publish", "lint"):
        assert command not in QUERY_COMMANDS, command


def test_report_counts_the_two_kinds_apart(home):
    record("show", target="C1")
    record("search", target="日志留存")
    record("draft")
    text = render_report(load())
    assert "Lookups 2" in text
    assert "production 1" in text


def test_report_prints_the_three_questions(home):
    """判据在手记里，不在计数器上。日志只防回忆。"""
    text = render_report(load())
    for fragment in ("What were you doing at the time",
                     "Did looking it up solve the problem",
                     "what would you have done"):
        assert fragment in text


def test_an_empty_log_says_so_without_claiming_death(home):
    """零调用只在有场景的前提下才是死亡信号。没场景的零调用是误杀。"""
    text = render_report([])
    assert "No records yet" in text


def test_report_shows_notes_verbatim(home):
    record("usage", note="想起它了，但直接问模型更快")
    assert "想起它了，但直接问模型更快" in render_report(load())


def test_days_filter_keeps_only_recent_entries(home):
    from datetime import datetime, timedelta, timezone

    old = Entry(
        at=datetime.now(timezone.utc) - timedelta(days=40), command="show", target="C1"
    )
    fresh = Entry(at=datetime.now(timezone.utc), command="show", target="C2")
    from framework_reader.usage import within_days

    assert [e.target for e in within_days([old, fresh], 14)] == ["C2"]


# ---------- 接进 CLI ----------

def test_a_real_query_gets_logged(home, tmp_path):
    from typer.testing import CliRunner

    from framework_reader.cli.main import app

    CliRunner().invoke(app, ["stats", "--db", str(tmp_path / "nope.sqlite")])
    assert [e.command for e in load()] == ["stats"]


def test_usage_itself_is_not_logged(home):
    from typer.testing import CliRunner

    from framework_reader.cli.main import app

    CliRunner().invoke(app, ["usage"])
    assert load() == []


def test_usage_note_lands_in_the_log(home):
    from typer.testing import CliRunner

    from framework_reader.cli.main import app

    result = CliRunner().invoke(app, ["usage", "--note", "翻 CSF 时用了一次"])
    assert result.exit_code == 0
    assert [e.note for e in load()] == ["翻 CSF 时用了一次"]


# ---------- 记下查的是哪一条 ----------

def test_target_is_the_first_bare_argument_after_the_command():
    from framework_reader.usage import target_from_argv

    argv = ["fr", "show", "NIST-CSF-2.0:DE.CM-01", "--db", "build/content.sqlite"]
    assert target_from_argv(argv, "show") == "NIST-CSF-2.0:DE.CM-01"


def test_flags_and_their_values_are_not_the_target():
    from framework_reader.usage import target_from_argv

    assert target_from_argv(["fr", "stats", "--db", "x.sqlite"], "stats") == ""


def test_a_missing_command_has_no_target():
    from framework_reader.usage import target_from_argv

    assert target_from_argv(["fr"], "show") == ""


def test_show_logs_which_control_was_looked_up(home, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from framework_reader.cli.main import app

    monkeypatch.setattr(
        "sys.argv", ["fr", "show", "NIST-CSF-2.0:DE.CM-01", "--db", str(tmp_path / "x")]
    )
    CliRunner().invoke(app, ["show", "NIST-CSF-2.0:DE.CM-01", "--db", str(tmp_path / "x")])
    assert [e.target for e in load()] == ["NIST-CSF-2.0:DE.CM-01"]


def test_the_suite_never_writes_to_the_real_reader_home():
    """跑测试灌满自用日志，会让唯一的验证信号失真——而且是往乐观方向失真。"""
    from pathlib import Path

    from framework_reader import usage

    # 不读进程环境（那条规矩钉在 tests/test_no_network_in_tests.py，
    # 而且它是子串匹配，连提到那个名字都会被判违规）。
    # 夹具生效与否，看 home() 指到哪就够了。
    assert usage.home() != Path.home() / ".framework_reader"
    assert "reader-home" in str(usage.home())
