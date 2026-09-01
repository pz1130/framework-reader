"""把用户库拷成一份一致性快照。网页服务化设计没写这一段——

服务开着时 WAL 在旁边长文件，直接读 `user.sqlite` 可能半截。
SQLite 自己的 backup API 会把 WAL 一并折进目标文件。
"""
import sqlite3
import tempfile
from pathlib import Path


def snapshot(path: Path | None = None) -> bytes:
    """打开用户库（没有就建空表），拷一份完整文件的字节。"""
    from framework_reader.userframework.store import connect, default_path

    src_path = Path(path) if path else default_path()
    src = connect(src_path, create=True)
    assert src is not None
    dest_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            dest_path = Path(tmp.name)
        dest = sqlite3.connect(dest_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
        return dest_path.read_bytes()
    finally:
        src.close()
        if dest_path is not None:
            dest_path.unlink(missing_ok=True)
