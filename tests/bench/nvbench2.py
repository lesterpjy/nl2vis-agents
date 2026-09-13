"""nvBench 2.0 (NeurIPS 2025 Datasets and Benchmarks) as a second dataset behind the seam.

What it is: 7,878 natural-language questions, each over **one table**, each with a set of two to five valid charts, because the
question was written to be ambiguous and every reading is gold. What it ships: no database and no SQL, only a schema string per
question and the charts as `{mark, encoding, transform}` objects; the tables are nvBench 1.0's, which VisEval ships as CSVs, and
the authors' JSON names each question's table (`csv_file`), which the parquet on Hugging Face drops. So the database each case
meets is that one table, built from the same CSV VisEval's databases are built from, with an introspected Schema Document.

Licence: none is stated on the Hugging Face card, in the repository, or in the paper beyond crediting nvBench 1.0 and BIRD, so
nothing of it is committed but case ids; the tables themselves are Spider's (CC BY-SA 4.0), through nvBench 1.0.

The published rows are the paper's Precision, Recall, F1 and Hit at K (Table 3), by the rule in `nvbench2_checks.py`. This system
answers one chart per Turn, so K is 1 and the @3 and @5 columns would repeat it. Its gold carries no query and no rows, so `ex`
and `correct` are not scored. What their metadata provided for the axis-swap rule (a field's type) is read off the table instead,
and a field is named in lower case, as their gold names it (871 of the 1,501 tables have a column their gold lower-cases).
"""

import csv
import functools
import hashlib
import json
import os
import random
import urllib.request
from pathlib import Path

import sqlglot
from sqlglot import exp

from data_agents.contracts import AnalysisResult, ChartSpec
from tests.bench import nvbench2_checks as checks
from tests.bench import tables, viseval
from tests.bench.case import Case

CACHE = Path(os.environ.get("BENCH_DIR", ".bench"))
URL = "https://raw.githubusercontent.com/HKUSTDial/nvBench-2.0/main/data/nvbench2.0/{split}.json"
THEIRS = ("dev", "test")  # the authors' splits; their train split is not downloaded, nothing here is trained
DEV = Path(__file__).parent / "nvbench2-dev.txt"  # the membership, committed by id before any number was looked at
DEV_QUOTA = {"bar": 20, "line": 16, "point": 16, "arc": 16, "rect": 16, "boxplot": 16}  # by the first gold chart's mark
SEED = 11
DESCRIPTION = "One table from the nvBench 2.0 benchmark."
SPLITS = "dev, test, test-authors, all"  # every held-out one keeps the prefix `runner.main`'s --final guard reads
MARKS = {"bar": "bar", "line": "line", "area": "line", "point": "point", "pie": "arc", "heatmap": "rect", "histogram": "bar"}
AGGREGATES = {exp.Sum: "sum", exp.Avg: "mean", exp.Min: "min", exp.Max: "max"}
SORTS = {"measure asc": "y", "measure desc": "-y"}


def cases(split: str, cache: Path = CACHE) -> list[Case]:
    """`dev` is the committed stratified 100 fixes may be found on; `test-authors` is the authors' own `test.json`, which our
    `dev` lies entirely outside of and which is therefore the fair held-out number to quote; `test` is everything outside our
    `dev`, so it blends their test with the remainder of their dev and is held out by our line rather than by theirs; `all` is
    both their files."""
    if split not in SPLITS.split(", "):
        raise ValueError(f"unknown nvBench 2.0 split {split!r}; one of {SPLITS}")
    every = _every(cache)
    if split == "all":
        return every
    if split == "test-authors":
        return [c for c in every if c.extra["their_split"] == "test"]
    ids = set(DEV.read_text().split())
    return [c for c in every if (c.id in ids) == (split == "dev")]


def register(cases: list[Case], cache: Path = CACHE) -> Path:
    databases = {c.database: build_database(c.extra["csv_file"], cache) for c in cases}
    return tables.save_registry(cache / "registry", databases, DESCRIPTION)


def score(case: Case, result, spec: ChartSpec | None, vega_lite: dict | None) -> dict:
    gold = case.extra
    chart = predicted(result, spec) if vega_lite is not None and isinstance(result, AnalysisResult) and spec else None
    verdict = checks.evaluate([chart] if chart else [], gold["gold"], gold["types"], k=1)
    return {"hit@1": bool(verdict["hit"]), "precision@1": verdict["precision"], "recall@1": verdict["recall"], "f1@1": verdict["f1"],
            "predicted": chart,
            "facets": {"gold charts": "+".join(sorted({g["mark"] for g in gold["gold"]})), "ambiguity level": str(len(gold["gold"]))}}


def predicted(result: AnalysisResult, spec: ChartSpec) -> dict | None:
    """Our answer in their vocabulary: the Chart Spec's roles over the SQL's projections, so a column the SQL aggregated is a
    channel with that aggregate and the WHERE clause is their `transform`. What has no spelling in their grammar keeps its own
    and simply does not match."""
    try:
        select = sqlglot.parse_one(result.sql, read="sqlite").find(exp.Select)
    except Exception:
        return None
    if select is None:
        return None
    named = {e.alias_or_name: e.unalias() for e in select.expressions}

    def channel(column: str) -> dict:
        return _channel(named[column]) if column in named else {"field": column}

    if spec.chart_type == "pie":
        encoding = {"theta": channel(spec.y), "color": channel(spec.x)}
        if spec.sort in SORTS:
            encoding["color"]["sort"] = SORTS[spec.sort].replace("y", "theta")
    elif spec.chart_type == "heatmap" and spec.color:
        encoding = {"x": channel(spec.x), "y": channel(spec.color), "color": channel(spec.y)}
    elif spec.chart_type == "histogram":
        encoding = {"x": channel(spec.y) | {"bin": {"maxbins": 10}}, "y": {"aggregate": "count"}}
    else:
        encoding = {"x": channel(spec.x), "y": channel(spec.y)}
        if spec.color:
            encoding["color"] = channel(spec.color)
        if spec.sort in SORTS:
            encoding["x"]["sort"] = SORTS[spec.sort]
    chart = {"mark": MARKS[spec.chart_type], "encoding": encoding}
    if filters := _filters(select.args.get("where")):
        chart["transform"] = [{"filter": f} for f in filters]
    return chart


def _channel(e: exp.Expression) -> dict:
    if isinstance(e, exp.Column):
        return {"field": e.name.lower()}
    if isinstance(e, exp.Count):
        return {"aggregate": "count"}
    if type(e) in AGGREGATES and (column := e.find(exp.Column)):
        return {"field": column.name.lower(), "aggregate": AGGREGATES[type(e)]}
    if (column := e.find(exp.Column)) and "'%Y'" in e.sql():  # strftime('%Y', col) and its kin: their year timeUnit
        return {"field": column.name.lower(), "timeUnit": "year"}
    if isinstance(e, (exp.Cast, exp.Distinct)) and (column := e.find(exp.Column)):
        return {"field": column.name.lower()}
    return {"field": e.sql(dialect="sqlite")}


MIRROR = {"lte": "gte", "gte": "lte", "lt": "gt", "gt": "lt"}  # the comparison read from the column's side, wherever it was written


def _filters(where) -> list[dict]:
    """Their filter per WHERE predicate: `=` and IN as `oneOf`, the comparisons by name, BETWEEN and a bounded pair as `range`."""
    if where is None:
        return []
    out: dict[str, dict] = {}
    for predicate in where.this.flatten() if isinstance(where.this, exp.And) else [where.this]:
        found = _comparison(predicate)
        if found is None:  # a predicate their grammar has no spelling for says nothing rather than a field with no operator
            continue
        name, operator, values = found
        field = out.setdefault(name, {"field": name})
        field[operator] = values if operator in ("oneOf", "range") else values[0]
    for field in out.values():
        if "gte" in field and "lte" in field:
            field["range"] = [field.pop("gte"), field.pop("lte")]
    return list(out.values())


def _comparison(predicate) -> tuple[str, str, list] | None:
    """(field, their operator, values), with the column's own side respected: `100 <= price` is price at least 100, not at most."""
    if isinstance(predicate, exp.Between) and isinstance(predicate.this, exp.Column):
        bounds = [v for v in (predicate.args.get("low"), predicate.args.get("high")) if isinstance(v, exp.Literal)]
        return (predicate.this.name.lower(), "range", [_literal(v) for v in bounds]) if len(bounds) == 2 else None
    if isinstance(predicate, exp.In) and isinstance(predicate.this, exp.Column) and predicate.expressions:
        values = [v for v in predicate.expressions if isinstance(v, exp.Literal)]
        return (predicate.this.name.lower(), "oneOf", [_literal(v) for v in values]) if len(values) == len(predicate.expressions) else None
    if type(predicate) not in (exp.EQ, exp.LTE, exp.GTE, exp.LT, exp.GT):
        return None
    left, right = predicate.this, predicate.expression
    flipped = isinstance(right, exp.Column) and isinstance(left, exp.Literal)
    column, value = (right, left) if flipped else (left, right)
    if not (isinstance(column, exp.Column) and isinstance(value, exp.Literal)):
        return None
    operator = type(predicate).__name__.lower()
    return column.name.lower(), "oneOf" if operator == "eq" else MIRROR[operator] if flipped else operator, [_literal(value)]


def _literal(v: exp.Literal):
    if v.is_string:
        return v.this
    number = float(v.this)
    return int(number) if number.is_integer() else number


def dataset(cache: Path = CACHE) -> Path:
    """The authors' dev and test JSON (3.3MB each), downloaded once from the repository; the only step that needs the network."""
    root = cache / "nvbench2"
    root.mkdir(parents=True, exist_ok=True)
    for split in THEIRS:
        if not (root / f"{split}.json").exists():
            urllib.request.urlretrieve(URL.format(split=split), root / f"{split}.json")
    return root


@functools.cache
def _every(cache: Path = CACHE) -> list[Case]:
    """Both their files as one pool, each case carrying which one it came from, so their splits stay addressable.

    Three rows across the two are the same (`csv_file`, question, gold) written twice and differ only in `steps`, the authors'
    own reasoning trace, which nothing here reads — so by the id, which hashes exactly what we do use, they are one case and
    are kept once. Counted both ways they would be paid for twice by the runner and counted twice in the table, while
    `paired` and `rescore` key by id and would count them once: a metric that disagrees with its own pairing."""
    root = dataset(cache)
    found: dict[str, Case] = {}
    for split in THEIRS:
        for raw in json.loads((root / f"{split}.json").read_text()):
            found.setdefault((case := _case(raw, split)).id, case)
    return sorted(found.values(), key=lambda c: c.id)


def _case(raw: dict, their_split: str) -> Case:
    gold = json.loads(raw["gold_answer"])
    db, table = raw["csv_file"].rsplit(".", 1)[0].split("@")
    return Case(id=hashlib.sha1(json.dumps([raw["csv_file"], raw["nl_query"], gold], sort_keys=True).encode()).hexdigest()[:10],
                dataset="nvbench2", database=f"{db}__{table}", question=raw["nl_query"],
                extra={"csv_file": raw["csv_file"], "gold": gold, "types": field_types(raw["csv_file"]), "their_split": their_split})


def csv_path(csv_file: str) -> Path:
    """The table's CSV, in VisEval's download: the one place the dataset is read from, whatever `cache` a build writes to."""
    db, table = csv_file.rsplit(".", 1)[0].split("@")
    return viseval.dataset() / "databases" / db / f"{table}.csv"


@functools.cache
def field_types(csv_file: str) -> dict[str, str]:
    """A field's type as their axis-swap rule needs it, read off the table's values: numeric is quantitative, the rest nominal."""
    rows = list(csv.reader(csv_path(csv_file).read_text(encoding="utf-8-sig").splitlines()))
    columns, body = rows[0], [r for r in rows[1:] if r]
    typed = [tables.column(values) for values in zip(*body)] if body else [[] for _ in columns]
    return {c.lower(): "quantitative" if tables.declare(v) != "TEXT" else "nominal" for c, v in zip(columns, typed)}


def build_database(csv_file: str, cache: Path = CACHE) -> Path:
    db, table = csv_file.rsplit(".", 1)[0].split("@")
    return tables.build(cache / "databases" / f"{db}__{table}.db", [csv_path(csv_file)])


def draw_dev(cache: Path = CACHE) -> list[str]:
    """Write `nvbench2-dev.txt`: a stratified draw from the authors' dev split, by the first gold chart's mark. Run once, by
    hand; from then on the committed file is the authority, for the same reason VisEval's `dev.txt` is."""
    pool = [_case(raw, "dev") for raw in json.loads((dataset(cache) / "dev.json").read_text())]
    rng, chosen = random.Random(SEED), []
    for mark, wanted in DEV_QUOTA.items():
        available = sorted((c for c in pool if c.extra["gold"][0]["mark"] == mark), key=lambda c: c.id)
        chosen += rng.sample(available, min(wanted, len(available)))
    return sorted(c.id for c in chosen)
