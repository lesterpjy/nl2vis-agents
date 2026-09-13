"""CSV tables into one SQLite file, so the agents meet a benchmark database through the same read-only handle as any Database.

VisEval ships nvBench's databases as folders of CSVs, and nvBench 2.0's tables are the same files, so both adapters build from
here: a typed column per CSV column, the foreign keys the files do not carry recovered from the data, and an introspected Schema
Document with structure and values but no prose.
"""

import csv
import json
import sqlite3
from pathlib import Path

from data_agents.data import registry, schema_document
from data_agents.data.schema_document import SchemaNotes
from data_agents.data.supplied import INTEGER

MIN_KEY_POOL = 5  # a foreign key inferred from values alone needs a target wide enough that the subset is not a coincidence


def build(path: Path, csvs: list[Path]) -> Path:
    """One SQLite file from CSV files, one table per file, named by the file's stem. Idempotent: an existing file stands."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded = {}
    for table in sorted(csvs):
        if table.stem.startswith("sqlite_"):  # a dump artefact, not a table anyone asks about
            continue
        rows = list(csv.reader(table.read_text(encoding="utf-8-sig").splitlines()))
        columns, body = rows[0], [r for r in rows[1:] if r]
        loaded[table.stem] = (columns, [column(values) for values in zip(*body)] if body else [[] for _ in columns])
    keys = infer_keys(loaded)
    connection = sqlite3.connect(path)
    for name, (columns, typed) in loaded.items():
        declared = [f'"{c}" {t}' for c, t in zip(columns, (declare(v) for v in typed))]
        declared += [f'FOREIGN KEY ("{c}") REFERENCES "{t}"("{tc}")' for (n, c), (t, tc) in keys.items() if n == name]
        connection.execute(f'CREATE TABLE "{name}" ({", ".join(declared)})')
        connection.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(columns))})', list(zip(*typed)))
    connection.commit()
    connection.close()
    return path


def infer_keys(loaded: dict[str, tuple[list[str], list[list]]]) -> dict[tuple[str, str], tuple[str, str]]:
    """The foreign keys VisEval's CSVs do not carry, recovered from the data itself.

    A wrong key is worse than no key, so the data decides and the name only breaks ties: the target column must be unique
    and non-null (an actual key), and every non-null value of the referencing column must appear in it. A matching name is
    enough on its own; without one, the target must hold at least `MIN_KEY_POOL` distinct values, because every column is a
    subset of a three-value lookup column by luck. Where several targets qualify, a named match wins and an ambiguous tie is
    dropped rather than guessed. Recovers 85% of the join conditions in VisEval's own gold SQL; paired over 100 cases, the
    keys moved `correct` by nothing, so the join cliff is semantics and not missing structure.
    """
    unique = {}  # (table, column) -> the set of values, for every column that could be the target of a key
    for name, (columns, typed) in loaded.items():
        for col, values in zip(columns, typed):
            present = [v for v in values if v is not None]
            if present and len(set(present)) == len(present) == len(values):
                unique[(name, col)] = set(present)
    keys = {}
    for name, (columns, typed) in loaded.items():
        for col, values in zip(columns, typed):
            present = {v for v in values if v is not None}
            if not present:
                continue
            hits = []
            for (target, column_of), pool in unique.items():
                if target == name or not present <= pool:
                    continue
                named = _names_match(col, target, column_of)
                if named or (len(pool) >= MIN_KEY_POOL and len(present) > 1):
                    hits.append((target, column_of, named))
            best = [hit for hit in hits if hit[2]] or hits  # a name beats a coincidence; nothing beats two equal candidates
            if len(best) == 1:
                keys[(name, col)] = best[0][:2]
    return keys


def _names_match(col: str, target_table: str, target_column: str) -> bool:
    """A foreign key is normally spelled as its target: the same column name, or the target's table or column woven into it."""
    col, target_table, target_column = col.lower(), target_table.lower(), target_column.lower()
    if col == target_column:
        return True
    stem = target_table.rstrip("s")
    return stem in col and any(hint in col for hint in ("id", "code", "no", "num", target_column))


def column(values: tuple[str, ...]) -> list:
    try:
        return [None if v == "" else int(v) if INTEGER.fullmatch(v.strip()) else float(v) for v in values]
    except ValueError:
        return [None if v == "" else v for v in values]


def declare(values: list) -> str:
    kinds = {type(v) for v in values if v is not None}
    return "INTEGER" if kinds == {int} else "REAL" if kinds <= {int, float} and kinds else "TEXT"


def document(name: str, path: Path, description: str) -> str:
    """The Schema Document introspected and not drafted: a model call per database would cost more than the run it serves, so
    the benchmark meets the agent with structure and values but no prose, which is a floor on what our documented databases get."""
    db = registry.open_database(registry.DatabaseEntry(name=name, url=f"sqlite:///{path}", schema_document=""))
    try:
        tables = schema_document.introspect(db)
        values = schema_document.enumerate_values(db, tables)
        return schema_document.render(name, description, tables, SchemaNotes(tables=[], gotchas=[]), values,
                                      schema_document.enumerate_examples(db, tables, values))
    finally:
        db.close()


def save_registry(directory: Path, databases: dict[str, Path], description: str) -> Path:
    """A registry of its own, holding one admin with a grant per database, with an introspected Schema Document for each."""
    (directory / "schemas").mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, path in sorted(databases.items()):
        entries[name] = registry.DatabaseEntry(name=name, url=f"sqlite:///{path}", description=description, schema_document=f"schemas/{name}.md")
        (directory / "schemas" / f"{name}.md").write_text(document(name, path, description))
    registry.save(entries, directory)
    (directory / "users.yaml").write_text(json.dumps({"users": [{"name": "admin", "role": "admin", "grants": sorted(entries)}]}))
    return directory
