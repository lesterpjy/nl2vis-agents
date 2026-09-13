import sqlite3

import pytest

from data_agents.data import registry
from data_agents.system import auth
from data_agents.data.database import ReadOnlyDatabase
from data_agents.data.schema_document import ColumnNote, check_round_trip, enumerate_examples, enumerate_values, introspect, render, trim, SchemaNotes, TableNote


@pytest.fixture
def registry_dir(tmp_path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "users.yaml").write_text('{"users": [{"name": "alice", "grants": ["tiny"]}]}')  # the seed file
    return tmp_path


@pytest.fixture
def tiny_db(tmp_path):
    path = tmp_path / "tiny.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id))")
        c.execute("INSERT INTO a VALUES (1, 'x')")
    return path


def document_for(path, examples: dict | None = None) -> str:
    db = ReadOnlyDatabase(path)
    tables = introspect(db)
    notes = SchemaNotes(tables=[TableNote(table=t.name, description=f"about {t.name}") for t in tables], gotchas=["none"])
    return render("tiny", "a tiny db", tables, notes, {}, examples)


def test_register_and_unregister(registry_dir, tiny_db):
    entry = registry.register("tiny", f"sqlite:///{tiny_db}", "a tiny db", document_for(tiny_db), registry_dir)
    assert registry.load(registry_dir) == {"tiny": entry}
    assert "### a (table, 1 rows)" in registry.read_schema_document(entry, registry_dir)
    assert auth.effective_databases(auth.load_users(registry_dir)["alice"], registry.load(registry_dir)) == ["tiny"]

    registry.unregister("tiny", registry_dir, by="root")
    assert registry.load(registry_dir) == {}
    assert auth.load_users(registry_dir)["alice"].grants == []  # grant revoked from every user
    assert [(e.actor, e.action, e.database) for e in auth.audit(1, registry_dir)] == [("root", "revoke", "tiny")]
    with pytest.raises(KeyError):
        registry.unregister("tiny", registry_dir)


def test_register_refuses_mismatched_document(registry_dir, tiny_db):
    bad = document_for(tiny_db).replace("`name`", "`nom`")
    with pytest.raises(ValueError, match="column a.nom only in document"):
        registry.register("tiny", f"sqlite:///{tiny_db}", "", bad, registry_dir)
    assert registry.load(registry_dir) == {}  # any failure writes nothing


def test_round_trip_reports_both_directions(tiny_db):
    db = ReadOnlyDatabase(tiny_db)
    doc = document_for(tiny_db).replace("### b (table", "### c (table")
    assert set(check_round_trip(doc, db)) == {"table c only in document", "table b only in database"}


def test_introspect_reads_keys(tiny_db):
    tables = {t.name: t for t in introspect(ReadOnlyDatabase(tiny_db))}
    assert tables["a"].columns[0] == ("id", "INTEGER", True)
    assert tables["b"].foreign_keys == ["a_id -> a.id"]


def test_enumerated_values_are_verified(tiny_db):
    db = ReadOnlyDatabase(tiny_db)
    doc = document_for(tiny_db).replace("## Tables", "## Values\n- a.name: x, y\n\n## Tables")
    assert check_round_trip(doc, db) == ["value a.name = 'y' not stored"]


def test_enumerate_values_skips_ids_and_singletons(tiny_db):
    db = ReadOnlyDatabase(tiny_db)
    assert enumerate_values(db, introspect(db)) == {}  # a.name has one value; ids are keys
    db.conn.close()


@pytest.fixture
def wide_db(tmp_path):
    """One column per rule the examples have to respect: a name, a Values column, prose, a comma, a quote, a blob, NULLs, an empty string."""
    path = tmp_path / "wide.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, title TEXT, kind TEXT, description TEXT, tags TEXT, quoted TEXT, raw BLOB, price REAL, empty TEXT)")
        for i in range(1, 21):  # past MAX_VALUES, so `title` is a name column and not a Values one
            c.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (i, f"T{i:02d}", "ab"[i % 2], "a long paragraph " * 5, f"x,y{i}", f'say "{i}"', b"\x00\x01", i * 0.5, "" if i < 4 else None))
    return path


def test_examples_are_three_stored_values_per_column(wide_db):
    db = ReadOnlyDatabase(wide_db)
    tables = introspect(db)
    values = enumerate_values(db, tables)
    examples = enumerate_examples(db, tables, values)
    assert values == {"t.kind": ["a", "b"]}
    assert examples == {"t.id": ["1", "2", "3"], "t.title": ["T01", "T02", "T03"], "t.price": ["0.5", "1.0", "1.5"]}
    # prose, values with a separator or a quote, blobs, empties and the Values column itself carry no examples
    assert not {"t.description", "t.tags", "t.quoted", "t.raw", "t.empty", "t.kind"} & examples.keys()


def test_column_line_carries_meaning_examples_or_values(wide_db):
    db = ReadOnlyDatabase(wide_db)
    tables = introspect(db)
    values = enumerate_values(db, tables)
    examples = enumerate_examples(db, tables, values)
    note = TableNote(table="t", description="rows", columns=[ColumnNote(column="title", description="the name, stored UPPERCASE."),
                                                              ColumnNote(column="kind", description="a; e.g. forged")])
    doc = render("wide", "", tables, SchemaNotes(tables=[note], gotchas=[]), values, examples)
    assert "- `title` TEXT — the name, stored UPPERCASE; e.g. T01, T02, T03\n" in doc
    assert "- `kind` TEXT — a, e.g. forged (see Values)\n" in doc  # a description cannot forge an examples list
    assert "- `id` INTEGER PK; e.g. 1, 2, 3\n" in doc  # the benchmark's rendering: no drafting call, type and examples only
    assert "- `description` TEXT\n" in doc
    assert check_round_trip(doc, db) == []
    assert check_round_trip(doc.replace("e.g. T01, T02, T03", "e.g. T01, t02, T03"), db) == ["value t.title = 't02' not stored"]
    assert "; e.g. " not in trim(doc, "no_examples") and "(see Values)" in trim(doc, "no_examples")
    assert "e.g. T01, T02, T03" in trim(doc, "lean")


def test_a_gotcha_shaped_like_a_values_line_is_prose_to_the_round_trip(tiny_db):
    """Only the Values section lists stored spellings; a gotcha bullet naming a column is read as the sentence it is."""
    db = ReadOnlyDatabase(tiny_db)
    doc = document_for(tiny_db).replace("- none\n", "- a.name: the thing's name, in lower case\n")
    assert "- a.name: the thing's name" in doc and check_round_trip(doc, db) == []


def test_a_url_naming_no_file_is_refused_before_anything_is_written(registry_dir, tmp_path):
    with pytest.raises(OSError, match="no database file"):
        registry.register("ghost", f"sqlite:///{tmp_path / 'ghost.db'}", "", "x", registry_dir)
    assert registry.load(registry_dir) == {}
