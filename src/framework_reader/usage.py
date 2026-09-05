"""The self-usage log. Main spec §7.3.1

Since 2026-08-22 this is the product's **only** validation signal: no judges for the blind test,
no calendar window for a scenario - all that remains is "when the next piece of real work happens, does this tool come to mind".

The log exists to **defend against recall** - recall is systematically optimistic here. The verdict
is not in the counter; it is in the three questions of a `--note`.
"""
import json
import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

# Only queries count as "using the tool". draft / interview / blindtest are **building** the tool;
# mixing the two is the most comfortable form of self-deception.
#
# 2026-08-24: search and frameworks added. It used to be {show, stats} only, yet `fr search` is
# the **main entrance** - the Skill's first instruction is "search first, then look"; not remembering
# control numbers is the norm. A main entrance booked under "production" makes this gauge record
# every real use as "you are developing": "queries: 0" would stay 0 forever, and that 0 means nothing.
#
# assess / gap / soa stay on the "production" side - real use, but not queries; mixing them in
# dilutes the one question this counter must answer (the third: without it, would you just ask the model).
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
    """The first argument after the command name is what was queried.

    Click's group callback cannot see the sub-command's arguments (not parsed yet at that point),
    so it reads from argv. Worst case it records an empty string; the command itself is unaffected.
    """
    if command not in argv:
        return ""
    rest = argv[argv.index(command) + 1:]
    for index, token in enumerate(rest):
        if token.startswith("-"):
            continue
        if index and rest[index - 1].startswith("-"):
            continue          # that is the previous flag's value, not the target
        return token
    return ""


def _load_dotenv() -> None:
    if "FRAMEWORK_READER_HOME" in os.environ:
        return
    candidates = [
        Path(".env"),
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
                if "FRAMEWORK_READER_HOME" in os.environ:
                    break
            except OSError:
                pass


def home() -> Path:
    _load_dotenv()
    raw = os.environ.get("FRAMEWORK_READER_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".framework_reader_en").resolve()


def log_path() -> Path:
    return home() / "usage.jsonl"


def record(command: str, *, target: str = "", note: str = "") -> None:
    """Append one line. **No failure here may ever fail the command** - querying a control must not crash because a log write failed."""
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
            continue          # half a line, or hand-mangled - skip rather than crash the report
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
