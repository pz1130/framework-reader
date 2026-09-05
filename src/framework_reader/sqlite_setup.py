"""The two things every SQLite connection must do after opening. See the web service design §6⑨

**Two things, not one.**

1. **WAL.** In the default rollback-journal mode, one writer blocks every reader.
   Fine for single-user local use; on the web service it is "one click, spin for ages". WAL lets reads and writes run in parallel.
2. **`busy_timeout`.** WAL only solves half of it: two writers still queue. The SQLite engine
   itself waits 0 ms by default - the second in line does not wait, it throws `database is locked` immediately.
   The Python driver sets 5 seconds for us (the `connect(timeout=5.0)` default),
   so today this is **not** broken. Writing it out here pins the value next to its explanation:
   the day someone opens connections another way (`timeout=0`, another driver, a pool),
   what gets lost should not be a default nobody knew they depended on.

**The content pack does not get WAL** (`writable=False`). It is a read-only file meant for distribution, and WAL would grow
`-wal` / `-shm` sidecar files next to it - "copy one file and it works" would instantly stop being true.

`synchronous` stays at the default FULL instead of the WAL-typical NORMAL: NORMAL can drop the last
transaction on power loss, and in this product "the last transaction" is likely someone's sign-off.
That much performance is not worth trading it for.
"""
import sqlite3

# Wait at most this long for a lock. 5s is enough for the previous write transaction to commit (writes here
# are single INSERTs), and short enough that a real deadlock does not hang the request thread. Matches the
# Python driver's default - deliberately aligned, not coincidence.
BUSY_TIMEOUT_MS = 5000


def prepare(conn: sqlite3.Connection, *, writable: bool = True) -> None:
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if not writable:
        return
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        # WAL cannot start on network filesystems (it needs shared memory). Then fall back to the default
        # journal mode and keep running - slow beats not connecting, and busy_timeout above is already set; that one matters more.
        pass
