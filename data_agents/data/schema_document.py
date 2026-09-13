"""Build and verify a Schema Document: introspected structure plus model-drafted meaning."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from data_agents.data.database import ReadOnlyDatabase


@dataclass
class Table:
    name: str
    kind: str  # table or view
    columns: list[tuple[str, str, bool]] = field(default_factory=list)  # name, type, is primary key
    foreign_keys: list[str] = field(default_factory=list)  # "col -> table.col"
    row_count: int = 0


def introspect(db: ReadOnlyDatabase) -> list[Table]:
    # Everything goes through the guarded handle: pragma_* table functions are plain SELECTs.
    names = db.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
    tables = []
    for name, kind in names.rows:
        t = Table(name=name, kind=kind)
        for _, col, ctype, _, _, pk in db.execute(f"SELECT * FROM pragma_table_info('{name}')").rows:
            t.columns.append((col, ctype or "ANY", bool(pk)))
        for row in db.execute(f"SELECT \"from\", \"table\", \"to\" FROM pragma_foreign_key_list('{name}')").rows:
            t.foreign_keys.append(f"{row[0]} -> {row[1]}.{row[2]}")
        t.row_count = db.execute(f'SELECT COUNT(*) FROM "{name}"').rows[0][0]
        tables.append(t)
    return tables


MAX_VALUES = 16
VALUE_SKIP = ("phone", "fax", "email", "address", "description", "notes", "password", "photo", "picture", "features", "postal", "first_name", "last_name", "firstname", "lastname", "username", "extension")
DATE = re.compile(r"^\d{4}-\d{2}")


def enumerate_values(db: ReadOnlyDatabase, tables: list[Table]) -> dict[str, list[str]]:
    """Low-cardinality text columns with their stored spellings: the literals a WHERE clause must match exactly (value linking)."""
    found: dict[str, list[str]] = {}
    for t in tables:
        if t.kind != "table" or t.row_count == 0:
            continue
        for col, ctype, pk in t.columns:
            if pk or not any(k in ctype.upper() for k in ("CHAR", "TEXT")) or any(word in col.lower() for word in VALUE_SKIP):
                continue
            rows = db.execute(f'SELECT DISTINCT "{col}" FROM "{t.name}" WHERE "{col}" IS NOT NULL ORDER BY 1 LIMIT {MAX_VALUES + 1}').rows
            values = [str(r[0]) for r in rows]
            tidy = all(len(v) <= 24 and "," not in v and not DATE.match(v) for v in values)
            if 1 < len(values) <= MAX_VALUES and tidy and not (len(values) > 2 and all(v.isdigit() for v in values)):
                found[f"{t.name}.{col}"] = values
    return found


EXAMPLES = 3  # M-Schema's example_num; XiYan's Table 6 puts per-column content at about two points
EXAMPLE_CHARS = 40  # past this a value is payload, not a name (chartable.LABEL_CHARS measured the same line)
PROSE = ("description", "notes", "comment", "password", "photo", "picture", "bio")


def enumerate_examples(db: ReadOnlyDatabase, tables: list[Table], values: dict[str, list[str]]) -> dict[str, list[str]]:
    """Three stored values per column, so the spelling and format a literal must take is visible without a gotcha; verified by
    the round-trip check like the Values Block. A column the Values Block lists says "(see Values)" instead; prose and blobs get none."""
    found: dict[str, list[str]] = {}
    for t in tables:
        if t.kind != "table" or t.row_count == 0:
            continue
        for col, _, _ in t.columns:
            key = f"{t.name}.{col}"
            if key in values or any(word in col.lower() for word in PROSE):
                continue
            # Only values the line can carry back through the parser: no separators, no quotes, no newlines, nothing long.
            rows = db.execute(f'SELECT DISTINCT "{col}" FROM "{t.name}" WHERE "{col}" IS NOT NULL AND TRIM("{col}") != \'\' '
                              f'AND "{col}" NOT LIKE \'%,%\' AND "{col}" NOT LIKE \'%"%\' AND "{col}" NOT LIKE \'%\' || char(10) || \'%\' '
                              f'AND LENGTH("{col}") <= {EXAMPLE_CHARS} ORDER BY 1 LIMIT {EXAMPLES}').rows
            if examples := [str(r[0]) for r in rows if not isinstance(r[0], bytes)]:
                found[key] = examples[:1] if DATE.match(examples[0]) else examples  # a date's format is the example; three show it thrice
    return found


class ColumnNote(BaseModel):
    column: str
    description: str = Field(description="A few words: what the column holds, its unit or format, what NULL means if it occurs. Never a claim the examples contradict.")


class TableNote(BaseModel):
    table: str
    description: str = Field(description="One or two sentences: what a row means and what the table is for in analysis.")
    columns: list[ColumnNote] = Field(default_factory=list, description="One per column, in the table's order.")


class SchemaNotes(BaseModel):
    tables: list[TableNote]
    gotchas: list[str] = Field(description="Facts that would otherwise cause wrong SQL: reserved-word names needing quotes, dates stored as text, money columns, joins that are easy to get wrong, which table holds revenue.")


STYLE = Path(__file__).with_name("schema_style.md")  # the template and the worked example a draft is held to, editable without code
DRAFT_MODEL = "openai:gpt-5.5"  # once per database, offline, and it sets the ceiling on every query ever asked of it


def draft_notes(name: str, description: str, tables: list[Table], values: dict[str, list[str]], examples: dict[str, list[str]],
                db: ReadOnlyDatabase | None = None) -> SchemaNotes:
    agent = Agent(os.environ.get("SCHEMA_MODEL", DRAFT_MODEL), output_type=SchemaNotes, instructions=STYLE.read_text())
    prompt = f"Database: {name}. {description}\n\n" + "\n".join(_observed(render_table(t, None, values, examples), t, db) for t in tables)
    return agent.run_sync(prompt).output


def _observed(rendered: str, t: Table, db: ReadOnlyDatabase | None) -> str:
    """What three example values cannot show and a draft would otherwise invent: how often a column is NULL, whether it holds
    one value on every row, and the span of a date column. The load-timestamp and two-timelines gotchas are only reachable from
    these, and counting them here keeps them facts rather than guesses."""
    if db is None or t.kind != "table" or t.row_count == 0:
        return rendered
    measured = ", ".join(f'SUM("{c}" IS NULL), COUNT(DISTINCT "{c}"), MIN("{c}"), MAX("{c}")' for c, _, _ in t.columns)
    row = db.execute(f'SELECT {measured} FROM "{t.name}"').rows[0]
    for (col, _, _), (nulls, distinct, low, high) in zip(t.columns, zip(*[iter(row)] * 4)):
        notes = [f"NULL on {nulls} of {t.row_count} rows"] if nulls else []
        if distinct == 1 and nulls < t.row_count and len(str(low)) <= EXAMPLE_CHARS:
            notes.append(f"same value on every row: {low}")
        elif distinct > 1 and DATE.match(str(low)):
            notes.append(f"{low} to {high}")
        if notes:
            rendered = rendered.replace(f"- `{col}` ", f"- `{col}` [{'; '.join(notes)}] ", 1)
    return rendered


def render_table(t: Table, note: TableNote | None, values: dict[str, list[str]], examples: dict[str, list[str]]) -> str:
    lines = [f"### {t.name} ({t.kind}, {t.row_count} rows)"]
    if note and note.description:
        lines.append(note.description)
    described = {c.column: c.description for c in note.columns} if note else {}
    lines += [_column_line(t.name, c, ctype, pk, described.get(c, ""), values, examples) for c, ctype, pk in t.columns]
    if t.foreign_keys:
        lines.append("FK: " + "; ".join(t.foreign_keys))
    return "\n".join(lines) + "\n"


def _column_line(table: str, col: str, ctype: str, pk: bool, description: str, values: dict, examples: dict) -> str:
    """M-Schema's per-column content in our syntax: name, type, key, meaning, three stored values or where to find them."""
    key = f"{table}.{col}"
    line = f"- `{col}` {ctype}{' PK' if pk else ''}"
    # A drafted sentence cannot forge the tails code appends below: it may not open an examples list, nor repeat the Values pointer.
    if words := " ".join(description.split()).removesuffix("(see Values)").strip().rstrip(".").replace("; e.g.", ", e.g."):
        line += f" — {words}"
    if key in values:
        return line + " (see Values)"
    if key in examples:
        return line + "; e.g. " + ", ".join(examples[key])
    return line


def render_values(values: dict[str, list[str]]) -> str:
    if not values:
        return ""
    return "\n## Values\nStored spellings of small text columns; a filter must match them exactly (SQLite = is case-sensitive).\n" + "".join(
        f"- {column}: {', '.join(vals)}\n" for column, vals in values.items())


def description_in(document: str) -> str:
    """The description `render` wrote on line 2, so re-registering a database keeps the words it was registered with."""
    lines = document.splitlines()
    return lines[1].strip() if len(lines) > 1 and not lines[1].startswith("#") else ""


def render(name: str, description: str, tables: list[Table], notes: SchemaNotes, values: dict[str, list[str]],
           examples: dict[str, list[str]] | None = None) -> str:
    by_table = {n.table: n for n in notes.tables}
    doc = f"# {name}\n{description}\n\n## Gotchas\n" + "".join(f"- {g}\n" for g in notes.gotchas)
    return doc + render_values(values) + "\n## Tables\n" + "\n".join(render_table(t, by_table.get(t.name), values, examples or {}) for t in tables)


def build(name: str, description: str, db: ReadOnlyDatabase) -> str:
    tables = introspect(db)
    values = enumerate_values(db, tables)
    examples = enumerate_examples(db, tables, values)
    return render(name, description, tables, draft_notes(name, description, tables, values, examples, db), values, examples)


VARIANTS = ("full", "no_examples", "lean")


def trim(document: str, variant: str) -> str:
    """Smaller variants of a document for the Analysis Agent's prompt; the cached file always stays full. `no_examples` drops the
    per-column examples; `lean` drops views and empty tables and keeps the examples, which are the content M-Schema parity added."""
    if variant == "full":
        return document
    if variant not in VARIANTS:
        raise ValueError(f"unknown schema document variant {variant!r}; choose from {VARIANTS}")
    kept, skipping = [], False
    for line in document.splitlines():
        if m := re.match(r"^### (\S+) \((table|view), (\d+) rows\)", line):
            skipping = variant == "lean" and (m.group(2) == "view" or m.group(3) == "0")  # nothing to query there
        if skipping:
            continue
        kept.append(EXAMPLES_TAIL.sub("", line) if variant == "no_examples" else line)
    return "\n".join(kept) + "\n"


EXAMPLES_TAIL = re.compile(r"; e\.g\. (.+)$")


def check_round_trip(document: str, db: ReadOnlyDatabase) -> list[str]:
    """Every table and column in the document exists in the database and vice versa; every listed value and example is stored as written."""
    documented: dict[str, set[str]] = {}
    values: dict[str, list[str]] = {}
    table, section = "", ""
    for line in document.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif m := re.match(r"^### (\S+) \(", line):
            table = m.group(1)
            current = documented.setdefault(table, set())
        elif section == "Tables" and (m := re.match(r"^- `([^`]+)`", line)):
            current.add(m.group(1))
            if tail := EXAMPLES_TAIL.search(line):
                values[f"{table}.{m.group(1)}"] = tail.group(1).split(", ")
        elif section == "Values" and (m := re.match(r"^- (\w+)\.(\w+): (.+)$", line)):
            values[f"{m.group(1)}.{m.group(2)}"] = m.group(3).split(", ")
    actual = {t.name: {c for c, _, _ in t.columns} for t in introspect(db)}
    mismatches = [f"table {t} only in {'document' if t in documented else 'database'}" for t in documented.keys() ^ actual.keys()]
    for t in documented.keys() & actual.keys():
        mismatches += [f"column {t}.{c} only in {'document' if c in documented[t] else 'database'}" for c in documented[t] ^ actual[t]]
    for column, listed in values.items():
        table, col = column.split(".")
        wanted = ", ".join("'" + v.replace("'", "''") + "'" for v in listed)  # asked for by name: the handle caps rows, a column does not
        stored = {str(r[0]) for r in db.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE CAST("{col}" AS TEXT) IN ({wanted})').rows}
        mismatches += [f"value {column} = {v!r} not stored" for v in listed if v not in stored]
    return mismatches
