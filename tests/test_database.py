import sqlite3

import pytest

from data_agents.data.database import QueryFailed, ReadOnlyDatabase, unique
from data_agents.data.sql_guard import SqlRejected


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (a INT)")
        c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    return ReadOnlyDatabase(path, row_cap=10, timeout_s=0.2)


def test_row_cap_marks_truncation(db):
    result = db.execute("SELECT a FROM t")
    assert len(result.rows) == 10 and result.truncated


def test_guard_runs_before_driver(db):
    with pytest.raises(SqlRejected):
        db.execute("DELETE FROM t")


def test_driver_is_read_only(tmp_path):
    # Even if the guard were bypassed, the connection itself refuses writes.
    db = ReadOnlyDatabase(tmp_path / "t.db") if (tmp_path / "t.db").exists() else None
    if db is None:
        sqlite3.connect(tmp_path / "t.db").execute("CREATE TABLE t (a INT)")
        db = ReadOnlyDatabase(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        db.conn.execute("INSERT INTO t VALUES (1)")


def test_timeout_interrupts(db):
    slow = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r"
    with pytest.raises(QueryFailed, match="exceeded"):
        db.execute(slow)


def test_sql_errors_become_query_failed(db):
    with pytest.raises(QueryFailed, match="no such table"):
        db.execute("SELECT * FROM missing")


@pytest.mark.parametrize("names,expected", [
    (["a", "b"], ["a", "b"]),
    (["name", "name"], ["name", "name_2"]),                       # SELECT c.name, l.name: the second used to overwrite the first
    (["name", "name", "name"], ["name", "name_2", "name_3"]),
    ([], []),
])
def test_duplicate_column_names_are_made_unique(names, expected):
    assert unique(names) == expected


def test_a_query_with_two_columns_of_one_name_keeps_both(db):
    """Every consumer keys rows by column name, so a repeat used to silently drop a column from the chart."""
    result = db.execute("SELECT a, a * 2 AS a FROM t")
    assert result.columns == ["a", "a_2"]
    assert dict(zip(result.columns, result.rows[0])) == {"a": 0, "a_2": 0}
    assert dict(zip(result.columns, result.rows[3])) == {"a": 3, "a_2": 6}
