"""Tier 1: the grain probe's rewrite, table-driven, no model and no database."""

import pytest

from data_agents.contracts import QueryResult
from data_agents.agents.grain import describe, differs, reading, side, twins

LEFT = "SELECT g.name, COUNT(t.id) AS n FROM genres AS g LEFT JOIN tracks AS t ON g.id = t.genre_id GROUP BY g.name"


@pytest.mark.parametrize("sql,expected", [
    (LEFT, {"join": "SELECT g.name, COUNT(t.id) AS n FROM genres AS g JOIN tracks AS t ON g.id = t.genre_id GROUP BY g.name",
            "count": "SELECT g.name, COUNT(DISTINCT t.id) AS n FROM genres AS g LEFT JOIN tracks AS t ON g.id = t.genre_id GROUP BY g.name"}),
    ("SELECT a FROM t LEFT OUTER JOIN u ON t.id = u.id", {"join": "SELECT a FROM t JOIN u ON t.id = u.id"}),
    ("SELECT a FROM t RIGHT JOIN u ON t.id = u.id FULL JOIN v ON v.id = t.id", {"join": "SELECT a FROM t JOIN u ON t.id = u.id JOIN v ON v.id = t.id"}),
    ("SELECT a, COUNT(DISTINCT b) FROM t GROUP BY a", {"count": "SELECT a, COUNT(b) FROM t GROUP BY a"}),
    ("SELECT a, COUNT(*) FROM t GROUP BY a", {}),                               # rows have no distinct form
    ("SELECT a, COUNT(a) FROM t GROUP BY a", {}),                               # and COUNT(DISTINCT a) grouped by a is always 1
    ("SELECT a, COUNT(DISTINCT a) FROM t GROUP BY a", {}),                      # from either side
    ("SELECT a, COUNT(b) FROM t GROUP BY a", {"count": "SELECT a, COUNT(DISTINCT b) FROM t GROUP BY a"}),
    ("SELECT a, COUNT(DISTINCT b, c) FROM t GROUP BY a", {}),                   # nor does a two-column DISTINCT a single one
    ("SELECT a FROM t JOIN u ON t.id = u.id", {}),                              # an inner join is the flip, not a knob
    ("SELECT COUNT(*) FROM t WHERE name LIKE '%LEFT JOIN%'", {}),               # the words in a string are not a join
    ("SELECT x FROM (SELECT a AS x, COUNT(b) AS n FROM t GROUP BY a)", {"count": "SELECT x FROM (SELECT a AS x, COUNT(DISTINCT b) AS n FROM t GROUP BY a)"}),
    ("not sql at all", {}),
])
def test_the_flipped_twins_of_a_statement(sql, expected):
    assert twins(sql) == expected


def test_the_same_rows_in_another_order_are_the_same_answer():
    a = QueryResult(columns=["g", "n"], rows=[["Rock", 3], ["Jazz", 1]])
    assert not differs(a, QueryResult(columns=["g", "n"], rows=[["Jazz", 1], ["Rock", 3]]))
    assert differs(a, QueryResult(columns=["g", "n"], rows=[["Rock", 3], ["Jazz", 1], ["Opera", 0]]))
    assert differs(a, QueryResult(columns=["g", "n"], rows=[["Rock", 4], ["Jazz", 1]]))


def test_both_numbers_are_named_briefly():
    assert describe(QueryResult(columns=["g", "n"], rows=[["Rock", 3]])) == "1 row (e.g. ['Rock', 3])"
    assert describe(QueryResult(columns=["g", "n"], rows=[[i, i] for i in range(10)])).startswith("10 rows (e.g. [0, 0], [1, 1], [2, 2])")
    assert describe(QueryResult(columns=["g"], rows=[])) == "0 rows"


def test_the_two_readings_are_illustrated_by_rows_that_actually_differ():
    """Two readings shown the same three rows show the model no difference at all, which is exactly what an outer join does
    whenever the rows it keeps sort below the head of the answer: 6 of 79 firings over the stored dev runs did that. The rows
    quoted are the ones the other reading does not return."""
    kept = QueryResult(columns=["g", "n"], rows=[["Carrowleagh", 1], ["Codling", 3], ["Gortahile", 1], ["Dublin Array", 0]])
    dropped = QueryResult(columns=["g", "n"], rows=[["Carrowleagh", 1], ["Codling", 3], ["Gortahile", 1]])
    said = reading("join", LEFT, kept, dropped)
    as_written, other = said.split("; ")
    assert "['Dublin Array', 0]" in as_written  # the row the other reading drops, not the three both share
    assert as_written.split("(e.g. ")[-1] != other.split("(e.g. ")[-1]


# Which side of a knob a statement took, which the benchmark once read off the gold SQL to answer a Clarification: no case
# list, and a statement that carries no knob at all took the plain side.

@pytest.mark.parametrize("knob,sql,expected", [
    ("join", LEFT, True),
    ("join", "SELECT a FROM t JOIN u ON t.id = u.id", False),
    ("join", "SELECT a FROM t", False),                                       # no join keeps nothing
    ("count", "SELECT a, COUNT(DISTINCT b) FROM t GROUP BY a", True),
    ("count", "SELECT a, COUNT(b) FROM t GROUP BY a", False),
    ("count", "SELECT a, COUNT(*) FROM t GROUP BY a", False),                 # rows, which is the plain side
    ("count", "SELECT a, SUM(b) FROM t GROUP BY a", False),                   # no COUNT collapses nothing
    ("count", "SELECT a, COUNT(DISTINCT a) FROM t GROUP BY a", False),        # the tautology is not a reading, so not a side
    ("join", "not sql at all", False),
])
def test_the_side_a_statement_took_on_each_knob(knob, sql, expected):
    assert side(knob, sql) is expected
