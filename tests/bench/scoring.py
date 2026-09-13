"""The rows every dataset fills, and how a case's rows are composed from them and the dataset's own.

The core is what a Turn's answer can be judged on without knowing the dataset: BIRD's `ex` and `correct` where the case's
gold SQL ran, whether a chart was drawn and whether it reads, and the asking path. Beside it sit the dataset's
published rows under the paper's own names, computed by the paper's rule, absent where its gold cannot judge them. Nothing
chooses among them: a row is present because the dataset's `score` returned it.

Two conditions, never blended: a case's top-level rows are its **first pass**, where a Turn that ends in a Clarification is an
unanswered question; `after` holds the same rows for the answer the user ends with once the simulated user has replied.
"""

from itertools import permutations

from data_agents.charts import chartable
from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from tests.bench.case import Case


def score(adapter, case: Case, result, spec: ChartSpec | None, vega_lite: dict | None) -> dict:
    """One Turn's rows: the core, then the dataset's own, then the spec kept so a scoring change can be re-run over it."""
    return core(case, result, vega_lite) | adapter.score(case, result, spec, vega_lite) | {"spec": spec.model_dump() if spec else None}


def core(case: Case, result, vega_lite: dict | None) -> dict:
    ex_, correct, scored_by = _correct(case, result)
    panel = chartable.panel(vega_lite) if vega_lite else None
    return {"id": case.id, "dataset": case.dataset, "database": case.database, "hardness": case.hardness,
            "ex": ex_, "correct": correct, "scored_by": scored_by,
            "charted": vega_lite is not None, "mark": panel["mark"]["type"] if panel else None,
            "reads": bool(vega_lite) and chartable.unreadable(vega_lite) is None}


def _correct(case: Case, result) -> tuple[bool | None, bool | None, str]:
    """BIRD's rule twice, and which ground truth was in a position to judge it: None where no gold ran, because a case with no
    executable gold is scored on the dataset's own rows alone, and a gold that errors is counted rather than scored."""
    if case.gold_rows is None:
        return None, None, "gold failed" if case.gold_sql else "no gold"
    if not isinstance(result, AnalysisResult):
        return False, False, "gold sql"
    return ex(result.table, case.gold_rows), projected(result.table, case.gold_rows), "gold sql"


def ex(agent: QueryResult, gold: QueryResult) -> bool:
    """BIRD's own evaluator, verbatim: `set(rows) == set(rows)`. Column order and count count, duplicates collapse, no tolerance."""
    return {tuple(r) for r in agent.rows} == {tuple(r) for r in gold.rows}


def projected(agent: QueryResult, gold: QueryResult) -> bool:
    """BIRD's set equality after projecting the agent's columns onto the gold's, in any order: some ordered selection of the
    agent's columns satisfies `ex`. Exact, so it is one set comparison per permutation, and the greedy matching the old tolerant
    rule needed cannot exist."""
    wanted = {tuple(r) for r in gold.rows}
    return any({tuple(r[i] for i in columns) for r in agent.rows} == wanted
               for columns in permutations(range(len(agent.columns)), len(gold.columns)))


def asked(events: list[dict]) -> bool:
    """Whether the first Turn ended in a Clarification, off its `turn_finished` event."""
    finished = next((e for e in events if e["kind"] == "turn_finished"), None)
    return bool(finished) and "question" in finished["result"]


COSTS = ("cost_usd", "requests", "seconds")  # what a run paid, kept beside the rows and never read as a score


def metrics(rows: list[dict]) -> list[str]:
    """Every key of the first row whose value is a verdict in every row (bool, None, or a number that is not a cost) and a
    verdict in at least one, in the row's own order: the core first, then whatever the dataset's `score` returned. The
    presentation reads these and never chooses them."""
    def verdict(value) -> bool:
        return value is None or isinstance(value, (bool, float))
    return [k for k in rows[0] if k not in COSTS and all(k in r and verdict(r[k]) for r in rows) and any(r[k] is not None for r in rows)] if rows else []
