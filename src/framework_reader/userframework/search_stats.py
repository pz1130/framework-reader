"""Bookkeeping for web search hits. The home page's "frequently searched" reads from here."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

# One search can return twenty hits; the first few entries of the catalog must not
# all get flipped to "frequent". Only the top few are recorded.
KEEP_PER_SEARCH = 5
WINDOW_DAYS = 90


def record(path: Path | None, control_ids: list[str], *,
           at: datetime | None = None, keep: int = KEEP_PER_SEARCH) -> None:
    ids = list(dict.fromkeys(control_ids))[:keep]
    if not ids:
        return
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    from framework_reader.userframework.store import connect

    conn = connect(path)
    assert conn is not None
    try:
        conn.executemany(
            "INSERT INTO search_hit (control_id, at) VALUES (?, ?)",
            [(cid, stamp) for cid in ids],
        )
        conn.commit()
    finally:
        conn.close()


def top(path: Path | None, *, limit: int = 8, days: int = WINDOW_DAYS) -> list[str]:
    from framework_reader.userframework.store import connect

    conn = connect(path)
    if conn is None:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            "SELECT control_id, COUNT(*) AS n FROM search_hit "
            "WHERE at >= ? GROUP BY control_id "
            "ORDER BY n DESC, control_id LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]
