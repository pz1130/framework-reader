"""Where the content pack lives.

The default is the relative path `build/content.sqlite` - right for `make build` then run inside the repo.

But once `fr` is on your PATH, the real cwd is wherever the document you are writing lives,
and a relative path points at nothing there. `FR_CONTENT_DB` gives an absolute path
so it runs from any directory. The name follows the existing `FRAMEWORK_READER_HOME`; no second convention.

**Read in exactly one place.** If the CLI and web shell read it separately, the two drift.
"""
import os
from pathlib import Path

DEFAULT_RELATIVE = Path("build/content.sqlite")


def content_db() -> Path:
    value = os.environ.get("FR_CONTENT_DB")
    return Path(value) if value else DEFAULT_RELATIVE
