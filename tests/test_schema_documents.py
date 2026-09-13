import pytest

from data_agents.data import registry
from data_agents.data.schema_document import EXAMPLES_TAIL, check_round_trip, enumerate_examples, enumerate_values, introspect, trim

ENTRIES = registry.load()


@pytest.mark.parametrize("name", list(ENTRIES), ids=list(ENTRIES))
def test_document_matches_database(name):
    entry = ENTRIES[name]
    db = registry.open_database(entry)
    try:
        assert check_round_trip(registry.read_schema_document(entry), db) == []
    finally:
        db.close()


@pytest.mark.parametrize("name", list(ENTRIES), ids=list(ENTRIES))
def test_every_column_line_carries_its_meaning_and_the_values_the_rules_give_it(name):
    """M-Schema parity is a property of the committed document, and the round-trip check does not test it: it says every example
    shown is stored, never that a column that should show three does. So the examples are derived again from the database and
    have to be the ones on the lines — which pins the rules beside them too: a Values column says so instead, a date shows one,
    prose and blobs show none, and every column line carries a description."""
    entry = ENTRIES[name]
    db = registry.open_database(entry)
    try:
        tables = introspect(db)
        expected = enumerate_examples(db, tables, enumerate_values(db, tables))
    finally:
        db.close()
    document = registry.read_schema_document(entry)
    shown, table = {}, ""
    for line in document.splitlines():
        if line.startswith("### "):
            table = line[4:].split(" ")[0]
        elif line.startswith("- `") and (tail := EXAMPLES_TAIL.search(line)):
            shown[f"{table}.{line.split('`')[1]}"] = tail.group(1).split(", ")
    assert shown == expected
    assert all(" — " in line for line in document.splitlines() if line.startswith("- `"))


@pytest.mark.parametrize("variant,drops_examples,keeps_views", [("full", False, True), ("no_examples", True, True), ("lean", False, False)])
def test_trim_variants_keep_every_column(variant, drops_examples, keeps_views):
    full = registry.read_schema_document(ENTRIES["sakila"])
    trimmed = trim(full, variant)
    assert ("; e.g. " not in trimmed) == drops_examples
    assert ("### film_list (view" in trimmed) == keeps_views and ("### film_text (table, 0 rows)" in trimmed) == keeps_views
    assert trimmed.count("- `") == full.count("- `") - (0 if keeps_views else sum(1 for _ in _view_columns(full)))
    assert "## Gotchas" in trimmed


def _view_columns(document: str):
    section = ""
    for line in document.splitlines():
        if line.startswith("### "):
            section = line
        elif line.startswith("- `") and ("(view" in section or ", 0 rows)" in section):
            yield line


def test_trim_rejects_unknown_variant():
    with pytest.raises(ValueError):
        trim("# x\n", "tiny")
