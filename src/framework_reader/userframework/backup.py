"""Copy the user store into a consistent snapshot. The hosted-service design does
not cover this -

While the service is running, WAL keeps growing in a side file, and reading
`user.sqlite` directly may catch it half-written. SQLite's own backup API folds
the WAL into the destination file.
"""
import sqlite3
import tempfile
from pathlib import Path


def snapshot(path: Path | None = None) -> bytes:
    """Open the user store (creating empty tables if absent) and copy the complete file's bytes."""
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
