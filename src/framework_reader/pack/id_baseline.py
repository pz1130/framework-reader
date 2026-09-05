"""control_id stability regression. spec §8②, §10.A"""
import json
import sqlite3
from pathlib import Path

BASELINE = Path("content/published_control_ids.json")


def snapshot(conn: sqlite3.Connection) -> list[str]:
    return sorted(r[0] for r in conn.execute("SELECT id FROM framework_control"))


def write_baseline(conn: sqlite3.Connection, path: Path = BASELINE) -> Path:
    path.write_text(
        json.dumps(snapshot(conn), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def check_baseline(conn: sqlite3.Connection, path: Path = BASELINE) -> list[str]:
    """Returns IDs that are "published but currently missing". An empty list means no drift.

    Newly added IDs are allowed; disappearing or renamed ones are not — a control removed
    by the framework should be marked deprecated and keep its row.
    """
    if not path.exists():
        return []
    published = set(json.loads(path.read_text(encoding="utf-8")))
    return sorted(published - set(snapshot(conn)))
