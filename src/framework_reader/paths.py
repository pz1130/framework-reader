"""内容包在哪。

默认是相对路径 `build/content.sqlite`——在仓库里 `make build` 完直接跑，就是这个。

但 `fr` 装进 PATH 之后，真实使用时的 cwd 是你正在写的那份文档所在的目录，
相对路径在那里指向一个不存在的文件。`FR_CONTENT_DB` 给一个绝对路径，
让它在任何目录下都能跑。命名跟着已有的 `FRAMEWORK_READER_HOME` 走，不另起一套。

**只在这一处读。** CLI 与 Web 壳各读各的，两处就会长得不一样。
"""
import os
from pathlib import Path

DEFAULT_RELATIVE = Path("build/content.sqlite")


def content_db() -> Path:
    value = os.environ.get("FR_CONTENT_DB")
    return Path(value) if value else DEFAULT_RELATIVE
