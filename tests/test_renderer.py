import pytest

from data_agents.charts import house_style
from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from data_agents.charts.chartable import single_row
from data_agents.charts.form import field_type
from data_agents.charts.presentation import axis_title
from data_agents.charts.renderer import build

RESULT = AnalysisResult(intent="comparison", sql="SELECT category, total FROM t", narrative="Sports leads.",
                        table=QueryResult(columns=["category", "total"], rows=[["Sports", 5314.2], ["Sci-Fi", 4756.9], ["Drama", 4587.4]]))
SPEC = ChartSpec(chart_type="bar", x="category", y="total", title="Sports films earn the most", subtitle="All time")


def colours_in(node) -> set[str]:
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("color", "fill", "stroke") and isinstance(value, str):
                found.add(value)
            found |= colours_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= colours_in(item)
    return found


def test_house_style_applied_after_the_spec():
    spec = build(SPEC, RESULT, "sakila")
    assert spec["config"] == house_style.CONFIG
    assert spec["config"]["font"] == house_style.FONT
    assert spec["title"]["subtitle"][-1] == house_style.caption("sakila", RESULT.sql)
    assert "$schema" in spec  # validated Vega-Lite


def test_only_palette_colours_appear():
    allowed = set(house_style.PALETTE) | set(house_style.SEQUENTIAL) | {house_style.SURFACE, house_style.INK, house_style.INK_SECONDARY, house_style.GRID}
    assert colours_in(build(SPEC, RESULT, "sakila")) <= allowed


def test_caption_names_database_and_sql_hash():
    caption = house_style.caption("chinook", "SELECT 1")
    assert caption.startswith("Source: chinook · SQL ") and house_style.BRAND in caption


def test_caption_drops_the_sql_hash_when_there_is_no_sql():
    assert house_style.caption("stations.csv") == f"Source: stations.csv · {house_style.BRAND}"


def test_many_categories_go_horizontal():
    wide = RESULT.model_copy(update={"table": QueryResult(columns=["category", "total"], rows=[[f"c{i}", i] for i in range(10)])})
    encoding = build(SPEC, wide, "sakila")["encoding"]
    assert encoding["y"]["field"] == "category" and encoding["x"]["field"] == "total"


def test_trend_keeps_time_order():
    trend = AnalysisResult(intent="trend", sql="s", narrative="n", table=QueryResult(columns=["Year", "Revenue"], rows=[["2009", 1.0], ["2010", 2.0]]))
    encoding = build(ChartSpec(chart_type="bar", x="Year", y="Revenue", title="t"), trend, "chinook")["encoding"]
    assert "sort" not in encoding["x"]


@pytest.mark.parametrize("bad", [ChartSpec(chart_type="bar", x="missing", y="total", title="t"), ChartSpec(chart_type="bar", x="total", y="category", title="t")])
def test_bad_columns_rejected(bad):
    with pytest.raises(ValueError):
        build(bad, RESULT, "sakila")


@pytest.mark.parametrize("values,expected", [([1, 2.5], "quantitative"), (["2009-01", "2010-02"], "temporal"), (["a", "b"], "nominal"), ([1, "a"], "nominal"), ([True, False], "nominal")])
def test_field_type(values, expected):
    assert field_type(values) == expected


@pytest.mark.parametrize("column,expected", [
    ("film_count", "film count"), ("OrdersHandled", "Orders Handled"), ("TotalAmount", "Total Amount"), ("total_spent", "total spent"),
    ("TotalUSD", "Total USD"), ("USDTotal", "USD Total"), ("CustomerId", "Customer Id"), ("n", "n"), ("revenue", "revenue"),
])
def test_axis_title(column, expected):
    assert axis_title(column) == expected


def test_the_axis_title_is_the_one_the_chart_carries():
    northwind_small = RESULT.model_copy(update={"table": QueryResult(columns=["category", "OrdersHandled"], rows=[["A", 3], ["B", 2], ["C", 1]])})
    spec = build(ChartSpec(chart_type="bar", x="category", y="OrdersHandled", title="t"), northwind_small, "northwind_small")
    assert spec["encoding"]["y"]["title"] == "Orders Handled"


@pytest.mark.parametrize("rows,expected", [([[195]], "195"), ([[1234567.5]], "1,234,567.5"), ([["R"]], "R"), ([[1, 2]], "v 1, w 2"), ([[1], [2]], None)])
def test_single_value(rows, expected):
    """One row is printed, never charted, whatever its columns hold: the invariant's row half (chartable)."""
    result = RESULT.model_copy(update={"table": QueryResult(columns=["v", "w"][: len(rows[0])], rows=rows)})
    assert single_row(result.table) == expected


ACTORS = AnalysisResult(intent="comparison", sql="s", narrative="n", table=QueryResult(
    columns=["first_name", "last_name", "films"], rows=[["GINA", "DEGENERES", 42], ["WALTER", "TORN", 41], ["MARY", "KEITEL", 40]]))
FILMS = AnalysisResult(intent="comparison", sql="s", narrative="n", table=QueryResult(
    columns=["title", "rating", "rentals"], rows=[["A", "PG", 30], ["B", "R", 28], ["C", "PG", 27], ["D", "G", 25]]))


def test_colour_unique_per_row_is_dropped():
    encoding = build(ChartSpec(chart_type="bar", x="first_name", y="films", color="last_name", title="t"), ACTORS, "sakila")["encoding"]
    assert "color" not in encoding and "xOffset" not in encoding  # a legend of one entry per bar says nothing


def test_colour_equal_to_x_is_dropped():
    encoding = build(ChartSpec(chart_type="bar", x="first_name", y="films", color="first_name", title="t"), ACTORS, "sakila")["encoding"]
    assert "color" not in encoding


def test_many_to_one_colour_is_kept_without_grouping():
    encoding = build(ChartSpec(chart_type="bar", x="title", y="rentals", color="rating", title="t"), FILMS, "sakila")["encoding"]
    assert encoding["color"]["field"] == "rating" and "xOffset" not in encoding  # one bar per title, coloured by rating: no offset slots
