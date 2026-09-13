"""Eight mechanical checks on the final SQL: the grain probe's two flips and six more faults that are silent (valid SQL, rows, a
clean chart), each decided by a one-edit rewrite of the parse tree executed through the same read-only handle and compared to
the original, so that no model judges anything and a retry fires only when the two derivations disagree (tests/fixtures/sql-checks.md).

Every check has a false-retry list that is empty or closed by a condition here: the rewrite keeps the original's ORDER BY and
LIMIT so no group outside the answer is judged, a documented SQLite behaviour (bare columns beside one MIN or MAX) is excluded
structurally, an intended floor (a bucket, x / k * k, a modulo companion, a cast to INTEGER) is excluded structurally, a twin
that does not run decides nothing, and a twin that ran but answered a different question (a cast that sorted a word as zero)
decides nothing either. What a GROUP BY names is read through its spelling (`sql_tree`), so an ordinal, an alias and a
qualification do not turn a tautology into a reading. Where the two derivations are two readings a question has to settle (a sum once per
row or once per thing, a filter that drops or keeps empties, a fraction or whole units), the retry hands both numbers back
once and the model decides by the question's words or asks; where they name a defect, the ladder gives two rungs.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from sqlglot import exp

from data_agents.agents import grain, sql_tree
from data_agents.agents.sql_tree import in_group, parse, projection as _projection, selects as _selects, sql_of as _sql
from data_agents.contracts import QueryResult

Execute = Callable[[str], QueryResult | None]  # the read-only handle; None when the twin does not run, which decides nothing

NUMBER = re.compile(r"^-?\d+(\.\d+)?$")  # a stored text that is a number, strictly: no exponents, no blanks
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # a literal that names a whole day


@dataclass
class Probe:
    """One check's run on one statement: whether its twin disagreed and, if so, the retry in the ladder's two parts."""

    check: str
    fired: bool
    problem: str = ""
    advice: str = ""
    detail: str = ""
    twin: str = ""  # the rewrite that was executed, for the record (tests/fixtures/sql-checks.md shows it beside the original)
    knobs: dict[str, bool] = field(default_factory=dict)  # the grain probe's per-knob agreement, for its own event


def message(probe: Probe) -> str:
    """The first rung's text, exactly as the model receives it."""
    return f"{probe.problem}. {probe.advice}"


def probes(sql: str, table: QueryResult, execute: Execute) -> list[Probe]:
    """Every check whose class the statement carries, run once each. Unparseable SQL is the guard's problem, not a check's."""
    tree = parse(sql)
    if tree is None:
        return []
    columns = _Columns(execute)
    out = [check(tree, table, execute, columns) for check in CHECKS.values()]
    return [p for p in out if p is not None]


def carries(check: str, sql: str) -> bool:
    """Whether the statement carries a check's class at all, read off the tree alone: the population a firing is counted over,
    and what "the next attempt fixed it" means to the reducer."""
    tree = parse(sql)
    return bool(tree is not None and CLASS[check](tree))


# --- shared readings of the tree -------------------------------------------------------------------------------------------


class _Columns:
    """Declared columns of the tables a statement names, read through the handle once per table (pragma_table_info is a SELECT)."""

    def __init__(self, execute: Execute):
        self.execute, self.known = execute, {}

    def of(self, table: str) -> list[tuple[str, str, bool]]:
        if table not in self.known:
            found = self.execute(f"SELECT name, type, pk FROM pragma_table_info('{table.replace(chr(39), chr(39) * 2)}')")
            self.known[table] = [(name, ctype or "", bool(pk)) for name, ctype, pk in found.rows] if found else []
        return self.known[table]

    def resolve(self, column: exp.Column, select: exp.Select) -> tuple[str, str] | None:
        """(alias, table) of a base table the column belongs to; None for a subquery's column or an unqualified name that
        several tables in scope could hold."""
        scope = _scope(select)
        if column.table:
            table = scope.get(column.table)
            return (column.table, table) if table else None
        holders = [(alias, table) for alias, table in scope.items() if table and column.name in {c for c, _, _ in self.of(table)}]
        return holders[0] if len(holders) == 1 else None

    def declared(self, column: exp.Column, select: exp.Select) -> str:
        found = self.resolve(column, select)
        return next((ctype for c, ctype, _ in self.of(found[1]) if c == column.name), "") if found else ""

    def key(self, table: str) -> list[str]:
        return [c for c, _, pk in self.of(table) if pk]


def _scope(select: exp.Select) -> dict[str, str | None]:
    """alias -> base table name for every source in the FROM and JOINs; None for a subquery."""
    sources = []
    if select.args.get("from_"):
        sources.append(select.args["from_"].this)
    sources += [join.this for join in select.args.get("joins") or []]
    return {s.alias_or_name: (s.name if isinstance(s, exp.Table) else None) for s in sources}


def _aggregates(select: exp.Select) -> list[exp.AggFunc]:
    """The select's own aggregates: not a window's, not a subquery's."""
    return [a for a in select.find_all(exp.AggFunc)
            if a.find_ancestor(exp.Select) is select and not a.find_ancestor(exp.Window)]


def _bare(select: exp.Select) -> list[int]:
    """Indexes of projections holding a column that is neither aggregated nor grouped, in an aggregate query. Excluded by
    SQLite's own rule: a query whose one aggregate is MIN or MAX, whose bare columns come from the extreme row by definition."""
    aggregates = _aggregates(select)
    if not aggregates and not select.args.get("group"):
        return []
    if len(aggregates) == 1 and isinstance(aggregates[0], (exp.Min, exp.Max)):
        return []
    grouped = sql_tree.grouped(select)
    out = []
    for i, p in enumerate(select.expressions):
        e = _projection(p)
        if in_group(e, grouped):
            continue
        if any(_bare_column(c, e, grouped) for c in e.find_all(exp.Column)):
            out.append(i)
    return out


def _bare_column(c: exp.Column, shown: exp.Expression, grouped: list[exp.Expression]) -> bool:
    node = c
    while node is not None:
        if in_group(node, grouped) or isinstance(node, (exp.AggFunc, exp.Window, exp.Subquery, exp.Select)):
            return False
        if node is shown:
            return True
        node = node.parent
    return True


def _with(select: exp.Select, root: exp.Expression) -> exp.Expression:
    """A nested select run standalone still needs the statement's CTEs."""
    twin = select.copy()
    if root is not select and root.args.get("with_"):
        twin.set("with_", root.args["with_"].copy())
    return twin


def _value(v):
    """One value as it compares: numbers by magnitude (2 and 2.0 are the same answer), everything else as text."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return round(float(v), 9)


def same_rows(a: QueryResult, b: QueryResult) -> bool:
    """Order-insensitive, numeric-tolerant: the answer a chart or a narrative would read is the same."""
    return sorted(str([_value(v) for v in r]) for r in a.rows) == sorted(str([_value(v) for v in r]) for r in b.rows)


def _shown(table: QueryResult, rows: list[list]) -> str:
    return f"{len(rows)} row{'s' if len(rows) != 1 else ''}" + (f" (e.g. {', '.join(str(r) for r in rows[:3])})" if rows else "")


# --- 1. a bare column beside an aggregate ---------------------------------------------------------------------------------

def bare_column(tree, table, execute, columns) -> Probe | None:
    """SQLite fills a selected column that is neither aggregated nor grouped from an arbitrary row of the group. The twin counts
    the distinct values (NULL counted as one) the column takes inside each group of the answer; one everywhere means the value
    was determined after all (GROUP BY a key with its name beside it), more means the number shown is arbitrary."""
    for select in _selects(tree):
        bare = _bare(select)
        if not bare or select.args.get("distinct"):
            continue
        twin = _with(select, tree)
        names = [select.expressions[i].alias_or_name for i in bare]
        for i in bare:
            e = _projection(select.expressions[i]).copy()
            twin.append("expressions", exp.Alias(this=exp.Add(this=exp.Count(this=exp.Distinct(expressions=[e])),
                                                              expression=exp.Paren(this=exp.GT(this=exp.Sub(this=exp.Count(this=exp.Star()),
                                                                                                             expression=exp.Count(this=e.copy())),
                                                                                                 expression=exp.Literal.number(0)))),
                                                  alias=exp.to_identifier(f"__bare_{i}")))
        result = execute(_sql(twin))
        if result is None:
            continue
        counts = [row[-len(bare):] for row in result.rows]
        loose = [row for row, c in zip(result.rows, counts) if any(n is not None and n > 1 for n in c)]
        fired = bool(loose)
        problem = advice = ""
        if fired:
            problem = (f"{', '.join(names)} in that SQL {'is' if len(names) == 1 else 'are'} neither aggregated nor in the GROUP BY, so SQLite "
                       f"fills {'it' if len(names) == 1 else 'them'} from an arbitrary row of each group, and in {len(loose)} of the "
                       f"{len(result.rows)} groups shown the group holds more than one value (e.g. {loose[0][:len(select.expressions)]} holds "
                       f"{max(n for n in counts[result.rows.index(loose[0])] if n is not None)})")
            advice = ("Either add the column to the GROUP BY, if each of its values deserves a row of its own, or replace it with the "
                      "aggregate that names the value the question wants (MAX, MIN, GROUP_CONCAT), or drop it.")
        return Probe("bare_column", fired, problem, advice, ", ".join(names), _sql(twin))
    return None


# --- 2. fan-out inflating a sum or an average -----------------------------------------------------------------------------

def fan_out(tree, table, execute, columns) -> Probe | None:
    """A SUM or AVG over a join that repeats the measured table's rows counts some of them more than once. The twin appends,
    under the same FROM, WHERE, GROUP BY, ORDER BY and LIMIT, the row count and the distinct count of the measured table's key
    (rowid where none is declared); a group of the answer with more rows than things is the fan-out. Whether once per row or
    once per thing is the reading the question meant is handed back with both numbers, as the grain probe does."""
    for select in _selects(tree):
        measured = _measured(select, columns)
        if not measured or select.args.get("distinct"):
            continue
        twin = _with(select, tree)
        for alias, table_name in measured:
            key = columns.key(table_name) or ["rowid"]
            ident = exp.column(key[0], table=alias)
            for part in key[1:]:  # every column of a composite key: two rows differing in the third are two rows, not one
                ident = exp.DPipe(this=ident, expression=exp.DPipe(this=exp.Literal.string(","), expression=exp.column(part, table=alias)))
            twin.append("expressions", exp.Alias(this=exp.Count(this=ident.copy()), alias=exp.to_identifier(f"__rows_{alias}")))
            twin.append("expressions", exp.Alias(this=exp.Count(this=exp.Distinct(expressions=[ident.copy()])), alias=exp.to_identifier(f"__things_{alias}")))
        result = execute(_sql(twin))
        if result is None:
            continue
        width = len(select.expressions)
        repeated = []
        for row in result.rows:
            for k, (alias, table_name) in enumerate(measured):
                rows_, things = row[width + 2 * k], row[width + 2 * k + 1]
                if rows_ and things and rows_ > things:
                    repeated.append((table_name, row[:width], rows_, things))
        fired = bool(repeated)
        problem = advice = ""
        if fired:
            table_name, label, rows_, things = repeated[0]
            problem = (f"the joins repeat rows of {table_name}, so the sum or average over it counts some of them more than once: in "
                       f"{len(repeated)} of the {len(result.rows)} groups shown (e.g. {label}: {rows_} rows over {things} distinct {table_name} rows)")
            advice = (f"If each {table_name} row should count once, aggregate {table_name} before joining it to the rest, or join on the "
                      "key that matches it to one row. If once per joined row is what the question means, finish again with the same "
                      "SQL and say so in the narrative.")
        return Probe("fan_out", fired, problem, advice, ", ".join(t for t, *_ in repeated) or ", ".join(t for _, t in measured), _sql(twin))
    return None


def _measured(select: exp.Select, columns: _Columns) -> list[tuple[str, str]]:
    """(alias, table) of every base table a SUM or AVG measures in a select that joins, when all of the aggregate's columns
    come from that one table."""
    if not select.args.get("joins"):
        return []
    out = []
    for agg in _aggregates(select):
        if not isinstance(agg, (exp.Sum, exp.Avg)):
            continue
        found = {columns.resolve(c, select) for c in agg.find_all(exp.Column)}
        if len(found) == 1 and None not in found:
            source = found.pop()
            if source not in out:
                out.append(source)
    return out


# --- 3. integer division --------------------------------------------------------------------------------------------------

def integer_division(tree, table, execute, columns) -> Probe | None:
    """A division of two integers drops the fraction in SQLite. The twin casts the numerator to REAL; the same rows mean every
    division was exact and nothing is said. A floor the SQL asks for is excluded structurally: a bucket the query groups by,
    x / k * k, a modulo by the same divisor beside it, or a cast of the quotient to INTEGER."""
    divisions = _integer_divisions(tree, columns)
    if not divisions:
        return None
    twin = tree.copy()
    for div in _integer_divisions(twin, columns):
        div.set("this", exp.Cast(this=div.this.copy(), to=exp.DataType.build("REAL")))
    result = execute(_sql(twin))
    if result is None:
        return None
    fired = not same_rows(table, result)
    problem = advice = ""
    if fired:
        shown = [d.sql(dialect="sqlite") for d in divisions]
        problem = (f"{', '.join(shown)} divides two integers, and SQLite drops the fraction, so the numbers shown are truncated: as "
                   f"written {_shown(table, table.rows)}; dividing exactly returns {_shown(result, result.rows)}")
        advice = ("If the question asks for a ratio, a share or an average, divide over a float (CAST(x AS REAL) / y, or 100.0 * x / y). "
                  "If whole units are the point, finish again with the same SQL and say so in the narrative.")
    return Probe("integer_division", fired, problem, advice, ", ".join(d.sql(dialect="sqlite") for d in divisions), _sql(twin))


def _integer_divisions(tree: exp.Expression, columns: _Columns) -> list[exp.Div]:
    out = []
    for select in _selects(tree):
        grouped = sql_tree.grouped(select)
        divisors_modded = {m.expression.sql() for m in select.find_all(exp.Mod) if m.find_ancestor(exp.Select) is select}
        for p in select.expressions:
            for div in _projection(p).find_all(exp.Div):
                if div.find_ancestor(exp.Select) is not select or not (_integer(div.this, select, columns) and _integer(div.expression, select, columns)):
                    continue
                if div.expression.sql() in divisors_modded or _floor(div, p, grouped):
                    continue
                out.append(div)
    return out


def _floor(div: exp.Div, shown: exp.Expression, grouped: list[exp.Expression]) -> bool:
    """The SQL asks for whole numbers: the quotient is grouped by, cast to an integer type, or multiplied back by its divisor."""
    node = div
    while node is not None and node is not shown.parent:
        if in_group(node, grouped):
            return True
        if isinstance(node, exp.Cast) and "INT" in node.to.sql().upper():
            return True
        if isinstance(node, exp.Mul) and node is not div and (node.this.sql() == div.expression.sql() or node.expression.sql() == div.expression.sql()):
            return True
        node = node.parent
    return False


def _integer(e: exp.Expression, select: exp.Select, columns: _Columns) -> bool:
    """Whether an operand is integer-typed by construction: an integer literal, a COUNT or LENGTH, a SUM, MIN or MAX of one, an
    arithmetic of them, or a column whose declared type has INTEGER affinity. Anything else (a REAL column, AVG, a float literal,
    a cast to REAL, a text) is not, and a division over it stays silent."""
    if isinstance(e, exp.Literal):
        return not e.is_string and "." not in e.this and "e" not in e.this.lower()
    if isinstance(e, (exp.Count, exp.Length)):
        return True
    if isinstance(e, (exp.Paren, exp.Neg)):
        return _integer(e.this, select, columns)
    if isinstance(e, (exp.Sum, exp.Min, exp.Max)):
        return _integer(e.this, select, columns)
    if isinstance(e, (exp.Add, exp.Sub, exp.Mul)):
        return _integer(e.this, select, columns) and _integer(e.expression, select, columns)
    if isinstance(e, exp.Cast):
        return "INT" in e.to.sql().upper()
    if isinstance(e, exp.Subquery) and isinstance(e.this, exp.Select) and len(e.this.expressions) == 1:  # a scalar subquery of one integer
        return _integer(_projection(e.this.expressions[0]), e.this, columns)
    if isinstance(e, exp.Column):
        return "INT" in columns.declared(e, select).upper()
    return False


# --- 4. a day-long literal bounding a timestamp ---------------------------------------------------------------------------

def date_bound(tree, table, execute, columns) -> Probe | None:
    """`col <= '2005-05-31'`, `col = D`, `col > D` or the upper bound of a BETWEEN, where the column stores instants, keeps or
    drops only the midnight of the day named. The twin moves each bound to the next day, exclusive; a column whose values carry
    no time part agrees by construction. A column of midnights spelled with a time is still cut, and firing on it is right."""
    if not _day_bounds(tree):
        return None
    twin = tree.copy()
    bounds = _day_bounds(twin)
    for node, column, literal in bounds:
        node.replace(_whole_day(node, column, literal))
    result = execute(_sql(twin))
    if result is None:
        return None
    fired = not same_rows(table, result)
    problem = advice = ""
    names = sorted({c.name for _, c, _ in bounds})
    if fired:
        problem = (f"the bound on {', '.join(names)} compares instants with a whole day ({', '.join(sorted({l.this for *_, l in bounds}))}), and the "
                   f"column holds times of day, so the day named is cut at its midnight: as written {_shown(table, table.rows)}; taking whole "
                   f"days returns {_shown(result, result.rows)}")
        advice = "Bound a timestamp with the next day, exclusive (col < '2005-06-01'), or compare date(col) with the day."
    return Probe("date_bound", fired, problem, advice, ", ".join(names), _sql(twin))


def _day_bounds(tree: exp.Expression) -> list[tuple[exp.Expression, exp.Column, exp.Literal]]:
    out = []
    for where in tree.find_all(exp.Where):
        for node in where.find_all(exp.LTE, exp.GTE, exp.GT, exp.LT, exp.EQ, exp.Between):
            found = _column_and_day(node)
            if found:
                out.append((node, *found))
    return out


def _column_and_day(node) -> tuple[exp.Column, exp.Literal] | None:
    def day(lit):
        return isinstance(lit, exp.Literal) and lit.is_string and DAY.match(lit.this) and _next_day(lit.this) is not None
    if isinstance(node, exp.Between):
        return (node.this, node.args["high"]) if isinstance(node.this, exp.Column) and day(node.args["high"]) else None
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and day(right) and isinstance(node, (exp.LTE, exp.GT, exp.EQ)):
        return left, right
    if isinstance(right, exp.Column) and day(left) and isinstance(node, (exp.GTE, exp.LT, exp.EQ)):
        return right, left
    return None


def _next_day(day: str) -> str | None:
    try:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    except ValueError:
        return None


def _whole_day(node, column: exp.Column, literal: exp.Literal) -> exp.Expression:
    nxt = exp.Literal.string(_next_day(literal.this))
    if isinstance(node, exp.Between):
        return exp.Paren(this=exp.And(this=exp.GTE(this=column.copy(), expression=node.args["low"].copy()), expression=exp.LT(this=column.copy(), expression=nxt)))
    if isinstance(node, exp.EQ):
        return exp.Paren(this=exp.And(this=exp.GTE(this=column.copy(), expression=literal.copy()), expression=exp.LT(this=column.copy(), expression=nxt)))
    if isinstance(node, (exp.LTE, exp.GTE)):  # col <= D, or D >= col: through the day
        return exp.LT(this=column.copy(), expression=nxt)
    return exp.GTE(this=column.copy(), expression=nxt)  # col > D, or D < col: after the day


# --- 5. a filter that drops empties ----------------------------------------------------------------------------------------

def null_filter(tree, table, execute, columns) -> Probe | None:
    """`col != x`, `col NOT IN (...)` and `col NOT LIKE x` are NULL for a row with no value, so the filter drops it. The twin
    keeps those rows (OR col IS NULL); the same rows mean the column holds none, or the SQL already decided (AND col IS NOT
    NULL). A comparison with the empty string is an emptiness filter and is not a reading."""
    if not _negatives(tree):
        return None
    twin = tree.copy()
    negatives = _negatives(twin)
    for node, column in negatives:
        node.replace(exp.Paren(this=exp.Or(this=node.copy(), expression=exp.Is(this=column.copy(), expression=exp.Null()))))
    result = execute(_sql(twin))
    if result is None:
        return None
    fired = not same_rows(table, result)
    names = sorted({c.name for _, c in negatives})
    problem = advice = ""
    if fired:
        problem = (f"the filter on {', '.join(names)} drops the rows where {'it is' if len(names) == 1 else 'they are'} empty (NULL), which the "
                   f"question may mean to keep: as written {_shown(table, table.rows)}; keeping them returns {_shown(result, result.rows)}")
        advice = ("If the question means everything except the value named, keep the empties (OR col IS NULL). If it means only rows "
                  "that have a value, finish again with the same SQL and say so in the narrative.")
    return Probe("null_filter", fired, problem, advice, ", ".join(names), _sql(twin))


def _negatives(tree: exp.Expression) -> list[tuple[exp.Expression, exp.Column]]:
    out = []
    for where in tree.find_all(exp.Where):
        for node in where.find_all(exp.NEQ, exp.Not, exp.Like, exp.ILike):
            column = _negated_column(node)
            if column is not None:
                out.append((node, column))
    return out


def _negated_column(node) -> exp.Column | None:
    if isinstance(node, exp.NEQ):
        sides = [node.this, node.expression]
    elif isinstance(node, (exp.Like, exp.ILike)):  # NOT LIKE is the LIKE node negated
        if not node.args.get("negate"):
            return None
        sides = [node.this, node.expression]
    elif isinstance(node.this, exp.In) and node.this.expressions and not node.this.args.get("query"):
        sides = [node.this.this, *node.this.expressions]
    else:
        return None
    column, *others = sides
    if not isinstance(column, exp.Column) or any(o.find(exp.Column) is not None for o in others):
        return None
    if any(isinstance(o, exp.Literal) and o.is_string and o.this == "" for o in others):
        return None
    return column


# --- 6. numbers stored as text, sorted as text ------------------------------------------------------------------------------

def text_sort(tree, table, execute, columns) -> Probe | None:
    """An ORDER BY on a projected column whose values in the answer are all numbers stored as text sorts them alphabetically, so
    '9' follows '10' and a top-N is the wrong N. The twin sorts on CAST(col AS REAL); the sequence of the key's values is
    compared, so tied keys in either order are the same answer. The answer is a window on the column and the cast is not, so a
    twin that pulled in a value the cast reads as zero sorted a different question and decides nothing."""
    if not isinstance(tree, exp.Select) or not tree.args.get("order"):
        return None
    keyed = _text_keys(tree, table)
    if not keyed:
        return None
    twin = tree.copy()
    for ordered, index in _text_keys(twin, table):
        ordered.set("this", exp.Cast(this=ordered.this.copy(), to=exp.DataType.build("REAL")))
    result = execute(_sql(twin))
    if result is None:
        return None
    indexes = [i for _, i in keyed]
    before = [[_number(r[i]) for i in indexes] for r in table.rows]
    after = [[_number(r[i]) for i in indexes] for r in result.rows]
    if any(v is None for row in after for v in row):
        return None  # the cast reached values outside the answer that are words, which it sorts as zero: the twin decides nothing
    fired = before != after
    names = [table.columns[i] for i in indexes]
    problem = advice = ""
    if fired:
        problem = (f"ORDER BY {', '.join(names)} sorts text, and every value of it is a number, so '9' sorts after '10': as written the "
                   f"order is {[r[indexes[0]] for r in table.rows[:5]]}; sorted as numbers it is {[r[indexes[0]] for r in result.rows[:5]]}")
        advice = "Sort on CAST(col AS REAL), and check the LIMIT again: the top rows are different."
    return Probe("text_sort", fired, problem, advice, ", ".join(names), _sql(twin))


def _text_keys(select: exp.Select, table: QueryResult) -> list[tuple[exp.Ordered, int]]:
    """ORDER BY terms that name a projected column whose answer values are numbers stored as text."""
    out = []
    for ordered in select.args["order"].expressions:
        index = _projected(ordered.this, select, table)
        if index is None:
            continue
        values = [r[index] for r in table.rows if r[index] is not None]
        if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, str) and NUMBER.match(v) for v in values) \
                and any(isinstance(v, str) for v in values):
            out.append((ordered, index))
    return out


def _projected(term: exp.Expression, select: exp.Select, table: QueryResult) -> int | None:
    """Which answer column an ORDER BY term is: by ordinal, by alias, by name, or by the same expression."""
    if isinstance(term, exp.Literal) and not term.is_string and term.this.isdigit() and 0 < int(term.this) <= len(table.columns):
        return int(term.this) - 1
    for i, p in enumerate(select.expressions):
        if i < len(table.columns) and (term.sql() == _projection(p).sql() or isinstance(term, exp.Column) and not term.table and term.name == p.alias_or_name):
            return i
    return None


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- the grain probe's two flips, in the same shape ------------------------------------------------------------------------

def grain_probe(tree, table, execute, columns) -> Probe | None:
    """An outer join, or a COUNT that could be COUNT(DISTINCT), decides what a row means; the flipped twin's rows decide whether
    the question has to (grain.py). A twin with no rows is not a reading either: an anti-join is an outer join by construction."""
    readings, knobs, agreed, twins = [], [], {}, []
    for knob, twin in grain.twins(_sql(tree)).items():
        other = execute(twin)
        if other is None:
            continue
        disagree = bool(other.rows) and grain.differs(table, other)
        agreed[knob] = not disagree
        if disagree:
            readings.append(grain.reading(knob, _sql(tree), table, other))
            knobs.append(knob)
            twins.append(twin)
    if not agreed:
        return None
    return Probe("grain", bool(readings), "the answer depends on a decision the question has to settle: " + "; ".join(readings),
                 "First look for the deciding word in the question itself: one that asks for distinct or unique things, for every row "
                 "or occurrence, for all of something including those with none, or only for those that match. If it is there the "
                 "question has decided: finish with the SQL that takes that reading and say the decision in the narrative, quoting the "
                 "word. Ask a Clarification, naming both readings in these words, only when no word in the question settles it.",
                 ", ".join(knobs), "\n".join(twins), agreed)


CHECKS = {"grain": grain_probe, "bare_column": bare_column, "fan_out": fan_out, "integer_division": integer_division,
          "date_bound": date_bound, "null_filter": null_filter, "text_sort": text_sort}

# Whether a statement carries each class at all, with no database: the reducer's "fixed" and the population a count is over.
CLASS = {
    "grain": lambda tree: grain.twins(_sql(tree)),
    "bare_column": lambda tree: any(_bare(s) for s in _selects(tree)),
    "fan_out": lambda tree: any(s.args.get("joins") and any(isinstance(a, (exp.Sum, exp.Avg)) for a in _aggregates(s)) for s in _selects(tree)),
    "integer_division": lambda tree: _integer_divisions(tree, _Columns(lambda sql: None)),
    "date_bound": _day_bounds,
    "null_filter": _negatives,
    "text_sort": lambda tree: isinstance(tree, exp.Select) and tree.args.get("order") is not None
    and any(isinstance(o.this, (exp.Column, exp.Literal)) for o in tree.args["order"].expressions),  # a named or numbered key, not an expression
}
