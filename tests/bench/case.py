"""One benchmark case, whatever the dataset.

The runner, the scoring core, the simulated user and the presentation read this and nothing else about a dataset. What a
dataset's gold means beyond an executable query lives in `extra`, which only that dataset's own `score` may read: the guard
against `extra` growing into an untyped second contract is that nothing outside `tests/bench/<dataset>.py` names a key of it.
"""

from pydantic import BaseModel

from data_agents.contracts import QueryResult


class Case(BaseModel):
    id: str
    dataset: str          # the adapter module's name under tests/bench/
    database: str         # the registered name the agents meet it under
    question: str
    paraphrases: list[str] = []
    gold_sql: str = ""    # empty where the dataset ships no query that runs; then `ex` and `correct` are not scored
    gold_rows: QueryResult | None = None  # the gold SQL's own rows through the read-only handle; None where it did not run
    hardness: str = ""
    extra: dict = {}      # the dataset's own gold, for its own `score`
