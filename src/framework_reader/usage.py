"""自用日志。主 spec §7.3.1

2026-08-22 起这是本产品**唯一**的验证手段：盲测无评委，日历窗口无场景，
剩下的只有「下一次真实工作发生时，它被不被想起」。

日志的作用是**防回忆**——回忆在这件事上系统性偏乐观。判据不在计数器上，
在 `--note` 手记里的那三问。
"""
import json
import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

# 查询才算「用这个工具」。draft / interview / blindtest 是**建**这个工具，
# 把两者混在一起算，是最舒服的一种自欺。
#
# 2026-08-24 补上 search 与 frameworks。原来只有 {show, stats}，而 `fr search`
# 是**主入口**——Skill 教的第一句就是「先搜，再看」，条号记不住是常态。
# 主入口落在「生产」那一边，等于这个仪表把每一次真实使用都记成「你在开发」：
# 「查询 0 次」会永远是 0，而那个 0 什么也不说明。
#
# assess / gap / soa 仍在「生产」那一边——它们是真实使用，但不是查询，
# 混进来会稀释掉这个计数器唯一要回答的问题（第三问：没有它你会不会直接问模型）。
QUERY_COMMANDS = {"show", "stats", "search", "frameworks"}

_THREE_QUESTIONS = (
    "What were you doing at the time? (A real scenario, not a test run.)",
    "Did looking it up solve the problem?",
    "Without this tool, what would you have done? Ask a model? Dig through the original text? Ask a person? Or not look it up at all?",
)


class Entry(BaseModel):
    at: datetime
    command: str
    target: str = ""
    note: str = ""


def target_from_argv(argv: list[str], command: str) -> str:
    """命令后面第一个不带横杠的参数，就是这次查的东西。

    Click 的 group callback 拿不到子命令的参数（那时还没解析），
    所以从 argv 取。取错了最坏是记成空字符串，不影响命令本身。
    """
    if command not in argv:
        return ""
    rest = argv[argv.index(command) + 1:]
    for index, token in enumerate(rest):
        if token.startswith("-"):
            continue
        if index and rest[index - 1].startswith("-"):
            continue          # 这是上一个 flag 的值，不是目标
        return token
    return ""


def home() -> Path:
    return Path(os.environ.get("FRAMEWORK_READER_HOME", Path.home() / ".framework_reader"))


def log_path() -> Path:
    return home() / "usage.jsonl"


def record(command: str, *, target: str = "", note: str = "") -> None:
    """追一行。**任何失败都不得让命令本身失败**——查一条控制不该因为日志写不进去而崩。"""
    entry = Entry(at=datetime.now(timezone.utc), command=command, target=target, note=note)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")
    except OSError:
        return


def load() -> list[Entry]:
    try:
        text = log_path().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Entry(**json.loads(line)))
        except ValueError:
            continue          # 半行、手改坏的行，跳过而不是让报告崩掉
    return out


def within_days(entries: Iterable[Entry], days: int) -> list[Entry]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [e for e in entries if e.at >= cutoff]


def render_report(entries: list[Entry]) -> str:
    calls = [e for e in entries if not e.note]
    notes = [e for e in entries if e.note]
    query = [e for e in calls if e.command in QUERY_COMMANDS]
    build = [e for e in calls if e.command not in QUERY_COMMANDS]

    lines = []
    if not entries:
        lines.append("No records yet.")
        lines.append(
            "  A zero count is a death signal only when there is a real scenario behind it; "
            "a zero with no scenario is a false alarm; do not draw conclusions from it."
        )
    else:
        lines.append(f"Lookups {len(query)} / production {len(build)}   (notes: {len(notes)})")
        lines.append("  Only lookups count as using the tool; production is building the tool. Do not add the two together.")
        if query:
            lines.append("")
            for entry in query[-10:]:
                target = f"  {entry.target}" if entry.target else ""
                lines.append(f"  {entry.at.astimezone():%Y-%m-%d %H:%M}  {entry.command}{target}")

    if notes:
        lines += ["", "Notes:"]
        for entry in notes:
            lines.append(f"  {entry.at.astimezone():%Y-%m-%d}  {entry.note}")

    lines += ["", "The evidence is in the notes, not the counters. After using it, answer the three questions (fr usage --note \"...\"):"]
    for question in _THREE_QUESTIONS:
        lines.append(f"  - {question}")
    lines.append("")
    lines.append('  The third question is the key: if eight times out of ten the answer is "I would just ask a model", ')
    lines.append("  you have verified yourself that the core selling point does not hold. Main spec §7.4")
    return "\n".join(lines)
