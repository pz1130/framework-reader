"""每条 SQLite 连接开出来之后要做的两件事。见网页服务化设计 §6⑨

**两件，不是一件。**

1. **WAL。** 默认的 rollback journal 模式下，一个人在写的时候读的人也被挡住。
   本机单人无所谓，网页服务上就是「点一下转半天」。WAL 让读写并行。
2. **`busy_timeout`。** WAL 只解决一半：两个写者仍然要排队。SQLite 引擎本身
   默认等 0 毫秒——排在后面的那个不是等，是立刻抛 `database is locked`。
   Python 的驱动会替我们把它设成 5 秒（`connect(timeout=5.0)` 的默认值），
   所以今天**并没有**坏。这里明写一遍，是把这个值钉在解释它的地方：
   哪天有人换一种方式建连接（`timeout=0`、别的驱动、连接池），
   丢掉的不该是一条谁都没意识到自己依赖过的默认值。

**内容包不开 WAL**（`writable=False`）。它是要分发的只读文件，WAL 会在它旁边
长出 `-wal` / `-shm` 两个附属文件，「拷一个文件就能用」立刻不成立。

`synchronous` 保持默认的 FULL，不调成 WAL 常配的 NORMAL：NORMAL 在掉电时可能
丢掉最后一个事务，而在这个产品里「最后一个事务」很可能是某个人的签字确认。
这点性能不值得拿它换。
"""
import sqlite3

# 等锁最多等这么久。5 秒足够让前一个写事务提交完（这里的写都是单条 INSERT），
# 又短到真出了死锁不会把请求线程一直挂着。与 Python 驱动的默认值一致——
# 是刻意对齐，不是巧合。
BUSY_TIMEOUT_MS = 5000


def prepare(conn: sqlite3.Connection, *, writable: bool = True) -> None:
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if not writable:
        return
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        # 网络文件系统上 WAL 开不起来（它要共享内存）。那时退回默认模式继续跑，
        # 慢总比连不上强——而 busy_timeout 上面已经设过了，那条更要紧。
        pass
