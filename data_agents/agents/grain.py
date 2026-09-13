"""The grain probe: the two knobs that decide what a row means, flipped and executed, so that agreement between two derivations
replaces a prompt rule.

An outer join and a COUNT that could be COUNT(DISTINCT) each change the answer only sometimes: on 83% of the queries that carry
one, flipping it returns the same rows, and a prompt sentence paid attention on every Turn to arbitrate nothing. Where the flip
does change the rows, the answer depends on a decision the question has to settle, and the check hands both readings back once
rather than the default the prompt could not know at write time. The comparison is bought from SQLite at one execution and no
model call.
"""

from sqlglot import exp

from data_agents.agents import sql_tree
from data_agents.contracts import QueryResult

# How each reading is said to a user who cannot read SQL, keyed by the knob and by the side the SQL as written took.
READINGS = {("join", True): ("keeps rows with no match, as zeros or blanks", "dropping them"),
            ("count", True): ("counts each value once", "counting every row"),
            ("count", False): ("counts every row", "counting each value once")}


def twins(sql: str) -> dict[str, str]:
    """The flipped twin of a statement per knob it carries: every outer join made inner, and every COUNT swapped between
    counting rows and counting distinct values. Empty when the SQL carries no knob, or does not parse (the guard's problem)."""
    tree = sql_tree.parse(sql)
    if tree is None:
        return {}
    out = {}
    if any(join.args.get("side") for join in tree.find_all(exp.Join)):
        flipped = tree.copy()
        for join in flipped.find_all(exp.Join):
            if join.args.get("side"):
                join.set("side", None)
                join.set("kind", None)
        out["join"] = flipped.sql(dialect="sqlite")
    if any(_countable(count) for count in tree.find_all(exp.Count)):
        flipped = tree.copy()
        for count in flipped.find_all(exp.Count):
            if _countable(count):
                count.set("this", count.this.expressions[0] if isinstance(count.this, exp.Distinct) else exp.Distinct(expressions=[count.this]))
        out["count"] = flipped.sql(dialect="sqlite")
    return out


def _countable(count: exp.Count) -> bool:
    """A COUNT with a second reading. COUNT(*) has no distinct form, a multi-column DISTINCT has no single-column one, and
    COUNT(g) grouped by g is always 1 under DISTINCT, which is a tautology and not a reading — whichever of the three ways the
    GROUP BY names g (sql_tree)."""
    inner = count.this
    if isinstance(inner, exp.Star) or (isinstance(inner, exp.Distinct) and len(inner.expressions) != 1):
        return False
    select = count.find_ancestor(exp.Select)
    counted = inner.expressions[0] if isinstance(inner, exp.Distinct) else inner
    return not (select and sql_tree.in_group(counted, sql_tree.grouped(select)))


def reading(knob: str, sql: str, table: QueryResult, other: QueryResult) -> str:
    """Both numbers, in plain words: what the SQL as written does and what the other reading returns. Carried by the retry so
    the Clarification the model may write reads as a decision a user can make ("counts each value once"), not as SQL."""
    as_written, flipped = READINGS[(knob, side(knob, sql))]
    return f"as written, the answer {as_written}: {describe(table, other)}; {flipped} instead returns {describe(other, table)}"


def side(knob: str, sql: str) -> bool:
    """Which side of a knob a statement takes, read off its tree: an outer join present, or DISTINCT inside a COUNT. A statement
    that carries neither took the plain side, since no outer join keeps nothing and no DISTINCT collapses nothing; that is how the
    benchmark's simulated user reads the gold SQL's own decision without a model."""
    tree = sql_tree.parse(sql)
    if tree is None:
        return False
    if knob == "join":
        return any(join.args.get("side") for join in tree.find_all(exp.Join))
    return any(isinstance(count.this, exp.Distinct) for count in tree.find_all(exp.Count) if _countable(count))


def differs(a: QueryResult, b: QueryResult) -> bool:
    """The same rows in any order are the same answer; the knob was a no-op."""
    return sorted(map(str, a.rows)) != sorted(map(str, b.rows))


def describe(table: QueryResult, other: QueryResult | None = None) -> str:
    """Both numbers, for the retry: how many rows, and enough of them to see what changed.

    The rows quoted are the ones the other reading does not return, because the head of the two answers is often identical —
    an outer join keeps its unmatched rows wherever they sort — and two readings illustrated by the same three rows show the
    model no difference at all. Where a reading returns nothing of its own, its own first rows say what it is.
    """
    rows = [row for row in table.rows if str(row) not in {str(r) for r in other.rows}] if other else []
    shown = ", ".join(str(row) for row in (rows or table.rows)[:3])
    return f"{len(table.rows)} row{'s' if len(table.rows) != 1 else ''}" + (f" (e.g. {shown})" if shown else "")
