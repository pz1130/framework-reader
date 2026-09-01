"""control_id 稳定性回归。spec §8②、§10.A"""
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
    """返回"已发布但当前缺失"的 ID。空列表代表未发生漂移。

    新增 ID 是允许的；消失或改名不允许——控制被框架删除应标 deprecated 保留行。
    """
    if not path.exists():
        return []
    published = set(json.loads(path.read_text(encoding="utf-8")))
    return sorted(published - set(snapshot(conn)))
