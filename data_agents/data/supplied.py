"""An Analysis Result built from data the user supplied, with no Analysis Agent call: their own SQL, or a CSV file."""

import csv
import re
from pathlib import Path

from data_agents.contracts import AnalysisResult, Intent, QueryResult
from data_agents.data.database import ReadOnlyDatabase

INTEGER = re.compile(r"[+-]?\d+")


def from_sql(db: ReadOnlyDatabase, sql: str, intent: Intent = "comparison") -> AnalysisResult:
    """The user's SQL runs through the same read-only handle and guard as the agent's, so nothing is trusted more."""
    return AnalysisResult(intent=intent, sql=sql, narrative="", table=db.execute(sql))


def from_csv(path: Path, intent: Intent = "comparison") -> AnalysisResult:
    return from_csv_text(path.read_text(), path.name, intent)


def from_csv_text(text: str, name: str, intent: Intent = "comparison") -> AnalysisResult:
    rows = [row for row in csv.reader(text.splitlines()) if row]
    if len(rows) < 2:
        raise ValueError(f"{name} needs a header row and at least one row of data")
    columns, body = rows[0], rows[1:]
    if any(len(row) != len(columns) for row in body):
        raise ValueError(f"{name} has rows that do not match its {len(columns)} columns")
    typed = [_column(values) for values in zip(*body)]
    return AnalysisResult(intent=intent, sql="", narrative="", table=QueryResult(columns=columns, rows=[list(row) for row in zip(*typed)]))


def _column(values: tuple[str, ...]) -> list:
    """A column is numeric only when every value in it parses; one stray word leaves the whole column text."""
    try:
        return [int(v) if INTEGER.fullmatch(v.strip()) else float(v) for v in values]
    except ValueError:
        return list(values)
