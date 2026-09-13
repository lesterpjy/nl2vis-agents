"""The readings of a SQLite parse tree that more than one check needs, so that no two checks read the same thing differently.

A GROUP BY term can be written three ways for the same column — by ordinal, by the projection's alias, or by the expression
itself — and an unqualified column names the one table in scope that holds it. A check that compares spellings instead of what
they stand for decides the same statement differently depending on how it was typed, which is how a tautology
(`COUNT(g)` grouped by `g`) escaped the exclusion that exists for it.
"""

import sqlglot
from sqlglot import exp


def parse(sql: str) -> exp.Expression | None:
    """The tree, or None: unparseable SQL is the guard's problem, not a check's."""
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return None


def sql_of(tree: exp.Expression) -> str:
    return tree.sql(dialect="sqlite")


def selects(tree: exp.Expression) -> list[exp.Select]:
    return list(tree.find_all(exp.Select))


def projection(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def resolved(term: exp.Expression, select: exp.Select) -> exp.Expression:
    """A GROUP BY or ORDER BY term as the expression it stands for: `1` is the first projection, `yr` is the projection aliased yr."""
    if isinstance(term, exp.Literal) and not term.is_string and term.this.isdigit() and 0 < int(term.this) <= len(select.expressions):
        return projection(select.expressions[int(term.this) - 1])
    if isinstance(term, exp.Column) and not term.table:
        for p in select.expressions:
            if isinstance(p, exp.Alias) and p.alias == term.name:
                return p.this
    return term


def grouped(select: exp.Select) -> list[exp.Expression]:
    """The GROUP BY terms, each as the expression it stands for."""
    return [resolved(g, select) for g in (select.args["group"].expressions if select.args.get("group") else [])]


def in_group(e: exp.Expression, terms: list[exp.Expression]) -> bool:
    """Whether an expression is one of the grouped terms, so its value is the same in every row of a group."""
    return any(e.sql() == term.sql() or _same_column(e, term) for term in terms)


def _same_column(a: exp.Expression, b: exp.Expression) -> bool:
    """The same column written once qualified and once not. SQLite refuses an unqualified name two tables in scope both hold,
    so an unqualified name is the only column of that name; two *qualified* names differing in their table are two columns."""
    return isinstance(a, exp.Column) and isinstance(b, exp.Column) and a.name == b.name and not (a.table and b.table)
