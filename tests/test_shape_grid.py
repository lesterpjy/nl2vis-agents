"""Tier 1: the chart invariant over every shape a result can take, not the ones anyone remembered to write down.

rows {1, 2, many} × label columns {0, 1, 2} × measures {0, 1, 2} × duplicated labels, each cell run through the same path the
orchestrator runs — the gate, the renderer, `unreadable` — and asserting exactly one of two outcomes: a chart that reads, or a
skip with a reason. No third outcome: no other exception, no unreadable chart passing through. The 1×N case that shipped a
bar with the same measure on both axes is one cell of this grid; nothing in the Dev Slice held it, because every VisEval
question groups by something.
"""

import itertools

import pytest

from data_agents.charts import chartable, renderer
from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult

ROWS = {"one": 1, "two": 2, "many": 9}
CELLS = [(r, labels, measures, dup) for r, labels, measures, dup in itertools.product(ROWS, (0, 1, 2), (0, 1, 2), (False, True))
         if labels + measures and not (dup and (labels == 0 or r == "one"))]


def table(rows: str, labels: int, measures: int, duplicated: bool) -> QueryResult:
    n = ROWS[rows]
    columns = [f"label_{i}" for i in range(labels)] + [f"measure_{i}" for i in range(measures)]
    body = [[("same" if duplicated else f"name {r}") if i < labels else float(r * 10 + i) for i in range(labels + measures)] for r in range(n)]
    return QueryResult(columns=columns, rows=body)


def judge(result: AnalysisResult) -> tuple[str, str]:
    """The orchestrator's path with a spec in place of the model's choice: the first label column on x, the first measure on
    y, a second label column as colour — the columns a well-behaved Visualization Agent would name."""
    t = result.table
    if not t.rows:
        return "skip", chartable.PLAIN["empty"]
    if chartable.single_row(t) is not None:
        return "skip", chartable.PLAIN["single_value"]
    labels = [c for c in t.columns if c.startswith("label")]
    measures = [c for c in t.columns if c.startswith("measure")]
    spec = ChartSpec(chart_type="bar", x=(labels or measures)[0], y=(measures or labels)[-1], color=labels[1] if len(labels) > 1 else None, title="t")
    try:
        vega_lite = renderer.build(spec, result, "grid")
    except ValueError as e:
        return "skip", str(e)
    return ("skip", why) if (why := chartable.unreadable(vega_lite)) else ("chart", "")


@pytest.mark.parametrize("rows,labels,measures,duplicated", CELLS, ids=[f"{r}-rows_{l}-labels_{m}-measures{'_dup' if d else ''}" for r, l, m, d in CELLS])
def test_every_cell_is_a_readable_chart_or_a_skip_with_a_reason(rows, labels, measures, duplicated):
    outcome, reason = judge(AnalysisResult(intent="comparison", sql="s", narrative="n", table=table(rows, labels, measures, duplicated)))
    assert (outcome, bool(reason)) in (("chart", False), ("skip", True))
    if rows == "one":
        assert outcome == "skip"  # one mark compares with nothing, whatever the columns
    if duplicated and labels == 1 and measures:
        assert outcome == "skip" and "repeats" in reason  # rows sharing one label with nothing to tell them apart


def test_the_one_by_n_case_prints_its_numbers():
    both = QueryResult(columns=["TotalSales", "TotalRevenue"], rows=[[1234, 5678.5]])
    assert chartable.single_row(both) == "TotalSales 1,234, TotalRevenue 5,678.5"
    with pytest.raises(ValueError, match="both x and y"):  # and had the model been asked anyway, the renderer refuses the self-comparison
        renderer.build(ChartSpec(chart_type="bar", x="TotalSales", y="TotalSales", title="t"),
                       AnalysisResult(intent="comparison", sql="s", narrative="n", table=both), "grid")


def test_one_column_never_plays_two_channels():
    assert chartable.two_channels(ChartSpec(chart_type="bar", x="a", y="a", title="t")) == "'a' is named for both x and y"
    assert chartable.two_channels(ChartSpec(chart_type="bar", x="a", y="b", color="b", title="t")) == "'b' is named for both color and an axis"
    assert chartable.two_channels(ChartSpec(chart_type="bar", x="a", y="b", color="c", title="t")) is None
