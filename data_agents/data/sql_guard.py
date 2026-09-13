"""Statement-level defense: only one read-only SELECT (or set operation) may reach the driver."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, TokenError

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Attach, exp.Detach, exp.Pragma, exp.Command, exp.Transaction, exp.Commit, exp.Rollback, exp.Set,
)
FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit"}


class SqlRejected(ValueError):
    pass


def guard(sql: str, limit: int) -> str:
    """Return the statement with a LIMIT, or raise SqlRejected. Parse failure rejects (fail closed)."""
    try:
        statements = sqlglot.parse(sql, read="sqlite", error_level=ErrorLevel.IMMEDIATE)
    except (ParseError, TokenError, ValueError) as e:
        raise SqlRejected(f"could not parse SQL: {e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise SqlRejected(f"only SELECT statements are allowed, got {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejected(f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Func) and (node.name or node.sql_name()).lower() in FORBIDDEN_FUNCTIONS:
            raise SqlRejected(f"function {node.name or node.sql_name()} is not allowed")
    if tree.args.get("limit") is None:
        tree = tree.limit(limit)
    return tree.sql(dialect="sqlite")
