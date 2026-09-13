import pytest

from data_agents.data.sql_guard import SqlRejected, guard

ACCEPTED = [
    ("plain select", "SELECT 1"),
    ("join and group", "SELECT c.name, COUNT(*) FROM film f JOIN category c ON c.id = f.cat GROUP BY c.name"),
    ("cte", "WITH t AS (SELECT 1 AS x) SELECT x FROM t"),
    ("union", "SELECT 1 UNION ALL SELECT 2"),
    ("subquery", "SELECT * FROM (SELECT 1) WHERE 1 IN (SELECT 1)"),
    ("quoted reserved table", 'SELECT * FROM "Order" LIMIT 5'),
    ("pragma table function", "SELECT name FROM pragma_table_info('actor')"),
    ("trailing semicolon", "SELECT 1;"),
]

REJECTED = [
    ("insert", "INSERT INTO t VALUES (1)"),
    ("update", "UPDATE t SET a = 1"),
    ("delete", "DELETE FROM t"),
    ("drop", "DROP TABLE t"),
    ("create", "CREATE TABLE t (a INT)"),
    ("alter", "ALTER TABLE t ADD COLUMN b INT"),
    ("attach", "ATTACH DATABASE '/tmp/x.db' AS x"),
    ("pragma statement", "PRAGMA table_info(actor)"),
    ("load_extension", "SELECT load_extension('evil')"),
    ("load_extension nested", "SELECT 1 FROM t WHERE x = (SELECT load_extension('evil'))"),
    ("write inside cte", "WITH x AS (INSERT INTO t VALUES (1)) SELECT 1"),
    ("multi statement", "SELECT 1; DROP TABLE t"),
    ("multi statement with comment", "SELECT 1; -- x\nDELETE FROM t"),
    ("vacuum", "VACUUM"),
    ("transaction", "BEGIN"),
    ("parse error", "SELECT * FROM t WHERE"),
    ("empty", ""),
    ("not sql", "please drop the table"),
]


@pytest.mark.parametrize("sql", [s for _, s in ACCEPTED], ids=[n for n, _ in ACCEPTED])
def test_accepts(sql):
    assert guard(sql, 10).upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize("sql", [s for _, s in REJECTED], ids=[n for n, _ in REJECTED])
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        guard(sql, 10)


def test_a_comment_cannot_be_broken_out_of_when_the_guard_rewrites_the_statement():
    """The guard returns the statement it re-rendered, not the text it was given, and a line comment comes back as a block one.
    So a comment holding `*/` is the one place untrusted text could close its own comment and be executed; sqlglot escapes it."""
    out = guard("SELECT 1 -- */ UNION SELECT 2", 5)
    assert out == "SELECT 1 /* * / UNION SELECT 2 */ LIMIT 5" and out.count("*/") == 1


def test_limit_injected_when_absent():
    assert guard("SELECT 1", 10).endswith("LIMIT 10")


def test_existing_limit_kept():
    assert guard("SELECT 1 LIMIT 3", 10).endswith("LIMIT 3")


def test_limit_on_union():
    assert guard("SELECT 1 UNION SELECT 2", 10).endswith("LIMIT 10")


# The determinism lint: a top-N with no order is a different answer on a different day.

@pytest.mark.parametrize("sql,fires", [
    ("SELECT name, n FROM t ORDER BY n DESC LIMIT 10", False),
    ("SELECT name, n FROM t LIMIT 10", True),
    ("SELECT name FROM t", False),
    # the word in a string or an alias is not a LIMIT, which is why this reads the parse tree and not the text
    ("SELECT 'limit' AS limit_note FROM t", False),
    # an inner LIMIT that is ordered, wrapped in an outer select that needs none
    ("SELECT * FROM (SELECT name FROM t ORDER BY n DESC LIMIT 5)", False),
    ("SELECT * FROM (SELECT name FROM t LIMIT 5)", True),
])
def test_a_limit_without_an_order_by_is_an_arbitrary_answer(sql, fires):
    from data_agents.agents.analysis import unordered_cut
    assert unordered_cut(sql) is fires


def test_unparseable_sql_is_the_guards_problem_and_not_the_lints():
    from data_agents.agents.analysis import unordered_cut
    assert unordered_cut("SELECT FROM WHERE LIMIT") is False
