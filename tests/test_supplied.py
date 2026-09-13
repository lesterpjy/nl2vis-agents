"""Tier 1: data the user supplies instead of the Analysis Agent. The SQL path is the guard's path, unchanged."""

import pytest

from data_agents.data import registry, supplied
from data_agents.data.sql_guard import SqlRejected

REJECTED = [
    "INSERT INTO film (title) VALUES ('x')",
    "UPDATE film SET title = 'x'",
    "ATTACH DATABASE 'other.db' AS other",
    "PRAGMA table_info('film')",
    "SELECT 1; SELECT 2",
    "WITH w AS (DELETE FROM film RETURNING film_id) SELECT * FROM w",
    "SELECT load_extension('evil.so')",
]


@pytest.fixture
def db():
    handle = registry.open_database(registry.load()["sakila"])
    yield handle
    handle.close()


@pytest.mark.parametrize("sql", REJECTED, ids=lambda s: s.split()[0].lower())
def test_supplied_sql_meets_the_same_guard_as_the_agents(db, sql):
    with pytest.raises(SqlRejected):
        supplied.from_sql(db, sql)


def test_supplied_sql_becomes_an_analysis_result_with_no_narrative(db):
    result = supplied.from_sql(db, "SELECT rating, COUNT(*) AS films FROM film GROUP BY rating", intent="distribution")
    assert result.intent == "distribution" and result.narrative == "" and len(result.table.rows) == 5
    assert result.table.columns == ["rating", "films"]


def write(tmp_path, text):
    path = tmp_path / "stations.csv"
    path.write_text(text)
    return path


def test_csv_columns_are_numeric_only_when_every_value_parses(tmp_path):
    result = supplied.from_csv(write(tmp_path, "station,rainfall,note\nDe Bilt,84,dry\nEelde,91.5,\nVlissingen,77,wet\n"))
    assert result.table.columns == ["station", "rainfall", "note"]
    assert [row[1] for row in result.table.rows] == [84, 91.5, 77]  # int stays int, one float makes the column float
    assert [row[0] for row in result.table.rows] == ["De Bilt", "Eelde", "Vlissingen"]
    assert result.table.rows[1][2] == ""  # one empty cell leaves the column text


def test_a_number_column_with_one_word_in_it_stays_text(tmp_path):
    result = supplied.from_csv(write(tmp_path, "station,rainfall\nDe Bilt,84\nEelde,n/a\n"))
    assert [row[1] for row in result.table.rows] == ["84", "n/a"]


@pytest.mark.parametrize("text", ["station,rainfall\n", "", "station,rainfall\nDe Bilt,84,extra\n"])
def test_unusable_csv_is_refused(tmp_path, text):
    with pytest.raises(ValueError):
        supplied.from_csv(write(tmp_path, text))
