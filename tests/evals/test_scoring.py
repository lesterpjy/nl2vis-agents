"""The correctness rule of tier 2, table-driven: unordered rows, numeric tolerance, extra columns tolerated, rows never."""

import pytest

from data_agents.contracts import QueryResult
from tests.evals.runner import same_result_set

GOLDEN = QueryResult(columns=["year", "revenue"], rows=[["2012", 477.53], ["2013", 450.58]])

CASES = [
    ("identical", QueryResult(columns=["Year", "Total"], rows=[["2012", 477.53], ["2013", 450.58]]), True),
    ("other row order", QueryResult(columns=["y", "r"], rows=[["2013", 450.58], ["2012", 477.53]]), True),
    ("other column order", QueryResult(columns=["r", "y"], rows=[[477.53, "2012"], [450.58, "2013"]]), True),
    ("year as integer", QueryResult(columns=["y", "r"], rows=[[2012, 477.53], [2013, 450.58]]), True),
    ("rounded within tolerance", QueryResult(columns=["y", "r"], rows=[["2012", 477.5], ["2013", 450.6]]), True),
    ("extra column", QueryResult(columns=["y", "n", "r"], rows=[["2012", 100, 477.53], ["2013", 90, 450.58]]), True),
    ("wrong number", QueryResult(columns=["y", "r"], rows=[["2012", 477.53], ["2013", 451.58]]), False),
    ("missing row", QueryResult(columns=["y", "r"], rows=[["2012", 477.53]]), False),
    ("extra row", QueryResult(columns=["y", "r"], rows=[["2012", 477.53], ["2013", 450.58], ["2011", 469.58]]), False),
    ("missing column", QueryResult(columns=["r"], rows=[[477.53], [450.58]]), False),
]


@pytest.mark.parametrize("agent,expected", [(a, e) for _, a, e in CASES], ids=[n for n, _, _ in CASES])
def test_same_result_set(agent, expected):
    assert same_result_set(agent, GOLDEN) is expected
