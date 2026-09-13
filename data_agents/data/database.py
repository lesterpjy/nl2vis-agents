"""One read-only handle to one SQLite database: the capability the Analysis Agent holds."""

import sqlite3
import threading
import time
from pathlib import Path

from data_agents.contracts import QueryResult
from data_agents.data.sql_guard import guard


class QueryFailed(ValueError):
    pass


def unique(names) -> list[str]:
    """Column names are keys everywhere downstream, and SELECT c.name, l.name returns 'name' twice: the second would
    overwrite the first and the chart would plot the wrong column. Suffix the repeats instead, so the model sees them too."""
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


class ReadOnlyDatabase:
    def __init__(self, path: str | Path, *, row_cap: int = 200, timeout_s: float = 5.0):
        self.path = Path(path)
        self.row_cap = row_cap
        self.timeout_s = timeout_s
        # Connection-level defense: read-only URI, query_only pragma, no extension loading.
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.execute("PRAGMA query_only = 1")
        if hasattr(self.conn, "enable_load_extension"):
            self.conn.enable_load_extension(False)
        self.lock = threading.Lock()  # tools may run in a worker thread

    def execute(self, sql: str) -> QueryResult:
        safe_sql = guard(sql, self.row_cap + 1)  # raises SqlRejected; one extra row reveals truncation
        deadline = time.monotonic() + self.timeout_s
        with self.lock:
            # A non-zero return from the progress handler interrupts the statement.
            self.conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            try:
                cursor = self.conn.execute(safe_sql)
                rows = cursor.fetchmany(self.row_cap + 1)
            except sqlite3.Error as e:
                if "interrupted" in str(e):
                    raise QueryFailed(f"query exceeded {self.timeout_s}s") from e
                raise QueryFailed(str(e)) from e
            finally:
                self.conn.set_progress_handler(None, 0)
        return QueryResult(columns=unique(d[0] for d in cursor.description or []),
                           rows=[list(r) for r in rows[: self.row_cap]], truncated=len(rows) > self.row_cap)

    def close(self) -> None:
        self.conn.close()

