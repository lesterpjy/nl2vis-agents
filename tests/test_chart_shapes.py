"""Tier 1: every shape a result table can take, through the renderer. The invariant is that what gets built can be read.

This table is the enumeration, not an example list: each row crosses one dimension a result can vary along (row count, category
count, series count, uniqueness, nulls, types, column spelling, truncation, measure magnitude, integrality, the unit a column
name implies, temporal granularity, grid density, distribution shape, correlation) against the chart built from it. A shape that
cannot be corrected states why, which is what the orchestrator skips on.
"""

import pytest

from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from data_agents.charts.chartable import panel, unreadable
from data_agents.charts.form import BAND, MAX_BARS
from data_agents.charts.renderer import build

LONG = "a very long track name that runs on " * 3
CJK = "音" * 14  # fifteen full-width glyphs per label: estimated at 101px in a 107px slot, and drawn as a smear


def result(columns, rows, intent="comparison", truncated=False) -> AnalysisResult:
    return AnalysisResult(intent=intent, sql="s", narrative="n", table=QueryResult(columns=columns, rows=rows, truncated=truncated))


def bar(x="cat", y="n", **fields) -> ChartSpec:
    return ChartSpec(chart_type="bar", x=x, y=y, title="t", **fields)


def line(x="t", y="n", **fields) -> ChartSpec:
    return ChartSpec(chart_type="line", x=x, y=y, title="t", **fields)


def months(n: int, start: int = 1) -> list:
    return [[f"{2009 + (start + i - 1) // 12}-{(start + i - 1) % 12 + 1:02d}", 35.6 + i] for i in range(n)]


def rows(n: int, label="cat {}") -> list:
    return [[label.format(i), n - i] for i in range(n)]


# name -> (result, spec, the reason it cannot be read, or None when the renderer corrects it)
SHAPES = {
    "one row":                    (result(["cat", "n"], [["A", 3]]), bar(), None),
    "two rows":                   (result(["cat", "n"], rows(2)), bar(), None),
    "six categories, vertical":   (result(["cat", "n"], rows(6)), bar(), None),
    "seven categories, flipped":  (result(["cat", "n"], rows(7)), bar(), None),
    "fifty categories":           (result(["cat", "n"], rows(50)), bar(), None),
    "two hundred categories":     (result(["cat", "n"], rows(200), truncated=True), bar(), None),
    "twelve series":              (result(["cat", "s", "n"], [[c, f"s{i}", i + 1] for c in "AB" for i in range(12)]), bar(color="s"), None),
    "six series":                 (result(["cat", "s", "n"], [[c, f"s{i}", i + 1] for c in "AB" for i in range(6)]), bar(color="s"), None),
    "colour unique per row":      (result(["cat", "s", "n"], [["A", "x", 1], ["B", "y", 2], ["C", "z", 3]]), bar(color="s"), None),
    "colour equal to x":          (result(["cat", "n"], rows(3)), bar(color="cat"), None),
    "nulls in the labels":        (result(["cat", "n"], [["A", 1], [None, 2], ["C", 3]]), bar(), None),
    "nulls in the measure":       (result(["cat", "n"], [["A", 1], ["B", None], ["C", 3]]), bar(), None),
    "very long labels":           (result(["cat", "n"], [[f"{c}{LONG}", i] for i, c in enumerate("ABC")]), bar(), None),
    # Found by rendering rather than by asserting: a full-width glyph is two narrow advances, and counting characters missed it.
    "full-width labels":          (result(["cat", "n"], [[f"{CJK}{c}", i] for i, c in enumerate("一二三四五六")]), bar(), None),
    "full-width prose":           (result(["cat", "note", "n"], [[f"c{i}", "音" * 60 + str(i), i] for i in range(4)]), bar(), None),
    "dotted column name":         (result(["c.name", "n"], rows(3)), bar(x="c.name"), None),
    "bracketed column name":      (result(["cat", "COUNT(*)"], rows(3)), bar(y="COUNT(*)"), None),
    "column name with a space":   (result(["cat", "film count"], rows(3)), bar(y="film count"), None),
    "camelCase column name":      (result(["cat", "OrdersHandled"], rows(3)), bar(y="OrdersHandled"), None),
    "quantitative x on a bar":    (result(["cat", "n"], [[2009, 1], [2010, 2], [2011, 3]]), bar(), None),
    "negative measures":          (result(["cat", "n"], [["A", -5], ["B", 3], ["C", -1]]), bar(), None),
    "one huge outlier":           (result(["cat", "n"], [["A", 1000000], ["B", 2], ["C", 3]]), bar(), None),
    "measures all equal":         (result(["cat", "n"], [["A", 7], ["B", 7], ["C", 7]]), bar(), None),
    "mixed-type labels":          (result(["cat", "n"], [["A", 1], [2, 2], [None, 3]]), bar(), None),
    "a title far too long":       (result(["cat", "n"], rows(3)), ChartSpec(chart_type="bar", x="cat", y="n", title="t " * 80), None),
    "trend over one point":       (result(["y", "n"], [["2009", 5]], intent="trend"), ChartSpec(chart_type="line", x="y", y="n", title="t"), None),
    "trend over years":           (result(["y", "n"], [["2009", 5], ["2010", 6], ["2011", 7]], intent="trend"), ChartSpec(chart_type="line", x="y", y="n", title="t"), None),
    # Found live: a numeric id as x bypassed every rule below, because they all asked for a nominal axis first.
    "identifier as the x axis":   (result(["customer_id", "name", "spend"], [[i, f"n{i}", 100 - i] for i in range(50)]), bar(x="customer_id", y="spend"),
                                   "'customer_id' is a number"),
    "few numeric x on a bar":     (result(["year", "n"], [[2009, 1], [2010, 2], [2011, 3]]), bar(x="year"), None),
    # Found live: a line drawn between countries in alphabetical order, with the labels overlapping into a smear.
    "line over categories":       (result(["country", "n"], rows(24, "country {}"), intent="trend"),
                                   ChartSpec(chart_type="line", x="country", y="n", title="t"), None),
    "line over years":            (result(["y", "n"], [["2009", 5], ["2010", 6]], intent="trend"), ChartSpec(chart_type="line", x="y", y="n", title="t"), None),
    "three labels too wide for x": (result(["cat", "n"], [[f"{c}{LONG}", i] for i, c in enumerate("ABC")]), bar(), None),
    # Found live: a legend with one entry, which says nothing that the title does not.
    "colour with one value":      (result(["cat", "s", "n"], [["A", "English", 1], ["B", "English", 2], ["C", "English", 3]]), bar(color="s"), None),
    # Not correctable: two rows carry one label and nothing says which is which. The model should have set a series column.
    "duplicate labels":           (result(["cat", "n"], [["A", 1], ["A", 2], ["B", 3]]), bar(), "repeats on the category axis"),
    # Too many series to colour and no room on the category axis to take them over: the grid is dense, so it is drawn as one.
    "many series, many labels":   (result(["cat", "s", "n"], [[f"c{c}", f"s{i}", i + 1] for c in range(10) for i in range(10)]), bar(color="s"), None),

    # Measure magnitude, integrality, and the unit a column name implies. Nothing here is a layout fault: the chart is legible
    # and says the wrong thing about its numbers, which is the class no layer owned before.
    "a measure in milliseconds":  (result(["track", "Milliseconds"], [[f"t{i}", 5286953 - i * 40000] for i in range(10)]), bar(x="track", y="Milliseconds"), None),
    "a measure in seconds":       (result(["job", "run_seconds"], [[f"job {i}", 7200 + i * 60] for i in range(5)]), bar(x="job", y="run_seconds"), None),
    "a measure in bytes":         (result(["file", "Bytes"], [[f"f{i}", 5_242_880 + i] for i in range(5)]), bar(x="file", y="Bytes"), None),
    "an integer count":           (result(["city", "customer_count"], [[f"city {i}", 1 + i % 2] for i in range(10)]), bar(x="city", y="customer_count"), None),
    "a percentage":               (result(["type", "track_share_percent"], [["A", 86.6], ["B", 6.9], ["C", 5.9], ["D", 0.3]]), bar(x="type", y="track_share_percent"), None),
    "millions, unitless":         (result(["cat", "n"], [[f"c{i}", 5_286_953 - i] for i in range(5)]), bar(), None),
    # Temporal granularity: ticks land on the buckets the data holds, and the labels that fit are the labels drawn.
    "twelve months":              (result(["month", "revenue"], months(12), intent="trend"), line(x="month", y="revenue"), None),
    "sixty months":               (result(["month", "revenue"], months(60), intent="trend"), line(x="month", y="revenue"), None),
    "thirty days":                (result(["day", "revenue"], [[f"2009-06-{d:02d}", 10 + d] for d in range(1, 31)], intent="trend"), line(x="day", y="revenue"), None),
    # Found by the benchmark: a date column nobody bucketed is a timestamp, and it used to type as an unordered category.
    "timestamps, unbucketed":     (result(["arrived", "dogs"], [[f"2017-09-{d:02d} 19:16:31", d] for d in range(1, 16)], intent="trend"),
                                   line(x="arrived", y="dogs"), None),
    # Distribution: raw values are bucketed by the renderer; buckets that arrive already named keep their own order.
    "raw values to bucket":       (result(["title", "length"], [[f"film {i}", 40 + (i * 7) % 140] for i in range(200)], intent="distribution"), bar(x="title", y="length"), None),
    "few distinct values":        (result(["film", "score"], [[f"f{i}", i % 8] for i in range(60)], intent="distribution"),
                                   ChartSpec(chart_type="histogram", x="film", y="score", title="t"), None),
    "buckets already named":      (result(["bucket", "films"], [["150+ min", 250], ["60-89 min", 224], ["120-149 min", 216], ["<60 min", 96]],
                                          intent="distribution"), bar(x="bucket", y="films"), None),
    "a histogram of buckets":     (result(["rating", "films"], [["G", 178], ["PG", 194], ["R", 195]], intent="distribution"),
                                   ChartSpec(chart_type="histogram", x="rating", y="films", title="t"), None),
    # Found by the benchmark: a scatter whose series carry one point each. Colour is the only thing naming those points.
    "a series per point":         (result(["avg_price", "series", "min_price"], [[112.5, "modern", 75], [90.0, "rustic", 40], [70.0, "plain", 20]]),
                                   ChartSpec(chart_type="point", x="avg_price", y="min_price", color="series", title="t"), None),
    # Correlation: two measures against each other. With an identifier for x the same shape has no labels at all and cannot be drawn.
    "two measures":               (result(["length", "rentals"], [[40 + i, 3 + (i * 3) % 20] for i in range(50)]), bar(x="length", y="rentals"), None),
    # Cardinality and density of the category-by-series grid.
    "a dense grid":               (result(["country", "year", "revenue"], [[f"country {c}", f"201{y}", c + y] for c in range(24) for y in range(5)]),
                                   bar(x="country", y="revenue", color="year"), None),
    "a sparse grid":              (result(["rating", "category", "films"], [[f"r{i % 5}", f"cat{i}", 10 + i] for i in range(12)]),
                                   bar(x="rating", y="films", color="category"), None),
    "a grid past the page":       (result(["cat", "s", "n"], [[f"c{c}", f"s{i}", c + i] for c in range(100) for i in range(5)]), bar(color="s"), None),
    # An order the user asked for in words. It outranks the house default where the axis has slots to rearrange, and is dropped
    # without a word where it has none, which is every continuous axis.
    "sort against the buckets":   (result(["bucket", "films"], [["150+ min", 250], ["60-89 min", 224], ["<60 min", 96]], intent="distribution"),
                                   bar(x="bucket", y="films", sort="category asc"), None),
    "sort against the magnitude": (result(["cat", "n"], rows(12)), bar(sort="category asc"), None),
    "sort a time axis cannot take": (result(["month", "revenue"], months(12), intent="trend"), line(x="month", y="revenue", sort="measure desc"), None),
    "sort a histogram cannot take": (result(["title", "length"], [[f"film {i}", 40 + (i * 7) % 140] for i in range(200)], intent="distribution"),
                                     bar(x="title", y="length", sort="category desc"), None),
    "sort a scatter cannot take": (result(["length", "rentals"], [[40 + i, 3 + (i * 3) % 20] for i in range(50)]), bar(x="length", y="rentals", sort="measure asc"), None),
    # Not correctable: the cells fit down the page but their column labels do not fit across it.
    "a grid too wide":            (result(["cat", "s", "n"], [[f"c{c}", f"series {i} {LONG[:40]}", c + i] for c in range(8) for i in range(5)]),
                                   bar(color="s"), "columns of cells do not fit"),
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_every_shape_is_either_corrected_or_explained(name):
    res, spec, expected = SHAPES[name]
    why = unreadable(build(spec, res, "db"))
    assert (why or "") .find(expected or "") >= 0 and bool(why) == bool(expected), f"{name}: {why!r}"


def test_an_order_the_question_asked_for_outranks_the_house_default():
    """The house sorts a comparison by magnitude; "the ten longest tracks in alphabetical order" asks for something else, and
    what was asked wins. Nothing is said on the chart about it, because it is what the user asked for."""
    twelve = result(["cat", "n"], rows(12))
    assert panel(build(bar(sort="category asc"), twelve, "db"))["encoding"]["y"]["sort"] == "ascending"  # flipped: labels run down y
    assert panel(build(bar(), twelve, "db"))["encoding"]["y"]["sort"] == "-x"                            # and the default is untouched
    assert panel(build(bar(sort="measure asc"), twelve, "db"))["encoding"]["y"]["sort"] == "x"           # the measure is x once flipped
    six = result(["cat", "n"], rows(6))
    assert panel(build(bar(sort="measure desc"), six, "db"))["encoding"]["x"]["sort"] == "-y"            # upright, the measure is y


def test_an_order_outranks_even_the_bucket_order():
    """'<60 min' before '60-89 min' is the rule for buckets nobody ordered; a user who asks for alphabetical gets alphabetical."""
    buckets = result(["bucket", "films"], [["150+ min", 250], ["60-89 min", 224], ["<60 min", 96]], intent="distribution")
    assert build(bar(x="bucket", y="films"), buckets, "db")["encoding"]["x"]["sort"] == ["<60 min", "60-89 min", "150+ min"]
    assert build(bar(x="bucket", y="films", sort="category asc"), buckets, "db")["encoding"]["x"]["sort"] == "ascending"


@pytest.mark.parametrize("spec,res", [
    (line(x="month", y="revenue", sort="measure desc"), result(["month", "revenue"], months(12), intent="trend")),
    (bar(x="length", y="rentals", sort="measure asc"), result(["length", "rentals"], [[40 + i, 3 + (i * 3) % 20] for i in range(50)])),
])
def test_an_order_a_continuous_axis_cannot_take_is_dropped(spec, res):
    """A time or measure scale positions its marks by value, so there are no slots to rearrange. Every one of the 45 stated
    orders the VisEval sweep does not draw is this case; the hint goes, the chart stays readable, and nothing is invented."""
    encoding = panel(build(spec, res, "db"))["encoding"]
    assert "sort" not in encoding["x"] and "sort" not in encoding["y"]


@pytest.mark.parametrize("n,expected_bars", [(7, 7), (20, 20), (50, MAX_BARS), (MAX_BARS, MAX_BARS), (MAX_BARS + 10, MAX_BARS), (200, MAX_BARS)])
def test_a_row_of_bars_gets_the_height_it_needs_and_no_more(n, expected_bars):
    vega_lite = build(bar(), result(["cat", "n"], rows(n)), "db")
    assert len(vega_lite["data"]["values"]) == expected_bars
    assert vega_lite["height"] / expected_bars >= BAND or vega_lite["height"] == 360  # 360 is the house height for a short chart


def test_the_reader_is_told_when_rows_were_dropped():
    subtitle = build(bar(), result(["cat", "n"], rows(200), truncated=True), "db")["title"]["subtitle"]
    assert any("capped at 200 rows" in line for line in subtitle)      # the row cap, which the chart used to hide
    assert any(f"{MAX_BARS} largest of 200" in line for line in subtitle)  # and the bars the renderer itself dropped


def test_a_line_over_categories_becomes_a_bar():
    """A line asserts an order to travel along; countries have none, so the mark is corrected and the flip rules then apply."""
    countries = result(["country", "n"], rows(24, "country {}"), intent="trend")
    vega_lite = build(ChartSpec(chart_type="line", x="country", y="n", title="t"), countries, "db")
    assert vega_lite["mark"]["type"] == "bar" and vega_lite["encoding"]["y"]["field"] == "country"  # flipped, so 24 labels have room


def test_a_grouped_band_widens_for_its_series():
    """Five bars sharing one 22px band are five hairlines, so the band grows with the number of series. A dense grid of that
    size is now a heatmap instead, so what is left for the widened band is the sparse grid, where most combinations have no bar."""
    sparse = result(["cat", "s", "n"], [[f"c{c}", f"s{(c + i) % 5}", c + i] for c in range(20) for i in range(2)])  # 40 of 100 cells
    assert build(bar(color="s"), sparse, "db")["height"] == 20 * 5 * 6  # SERIES_BAND per series, not one 22px band for all five
    plain = result(["cat", "n"], rows(20))
    assert build(bar(), plain, "db")["height"] == 20 * 22  # without series, one band each


def test_a_scatter_keeps_a_colour_that_names_one_point_each():
    """Beside a labelled axis a legend with one entry per row is noise, and it is dropped. On a scatter both axes are measures
    and nothing else names the points, so the same legend is the only identity the reader has: 21 of VisEval's 32 grouped
    scatters are this shape, and dropping their colour left identical dots."""
    points = result(["avg_price", "series", "min_price"], [[112.5, "modern", 75], [90.0, "rustic", 40], [70.0, "plain", 20]])
    spec = ChartSpec(chart_type="point", x="avg_price", y="min_price", color="series", title="t")
    assert panel(build(spec, points, "db"))["encoding"]["color"]["field"] == "series"
    bars = result(["cat", "s", "n"], [["A", "x", 1], ["B", "y", 2], ["C", "z", 3]])
    assert "color" not in build(bar(color="s"), bars, "db")["encoding"]  # the bar axis already names them, so the legend goes


def test_a_single_valued_colour_is_dropped():
    one = result(["cat", "s", "n"], [["A", "English", 1], ["B", "English", 2], ["C", "English", 3]])
    assert "color" not in build(bar(color="s"), one, "db")["encoding"]  # a legend of one entry says nothing


def test_labels_too_wide_for_the_x_axis_flip_rather_than_overlap():
    wide = result(["cat", "n"], [[f"{c}{LONG}", i] for i, c in enumerate("ABC")])
    assert build(bar(), wide, "db")["encoding"]["y"]["field"] == "cat"  # only three categories, but they do not fit across 640px


def test_a_full_width_glyph_is_measured_as_two_narrow_ones():
    """Six labels of fifteen CJK characters: 101 estimated pixels in a 107-pixel slot, so every rule passed and the chart
    shipped as a smear (`charts/probe-cjk-labels.png`). Counting characters was the bug; counting advances flips it."""
    cjk = result(["cat", "n"], [[f"{CJK}{c}", i] for i, c in enumerate("一二三四五六")])
    assert build(bar(), cjk, "db")["encoding"]["y"]["field"] == "cat"  # flipped, so each label has a row of its own
    assert unreadable(build(bar(), cjk, "db")) is None


def test_a_paragraph_of_full_width_glyphs_is_prose_not_a_name():
    """The same 2x error the other way round: forty CJK characters is a paragraph, and it used to count as a label."""
    from data_agents.charts.chartable import names_every_row
    assert names_every_row(["音" * 12 + str(i) for i in range(4)])      # a name, long but a name
    assert not names_every_row(["音" * 60 + str(i) for i in range(4)])  # payload


def test_more_series_than_colours_swaps_the_roles_when_the_axis_has_room():
    """Twelve series over two categories is twelve recycled colours; drawn the other way round it is twelve bars in two colours."""
    twelve = result(["cat", "s", "n"], [[c, f"s{i}", i + 1] for c in "AB" for i in range(12)])
    encoding = build(bar(color="s"), twelve, "db")["encoding"]
    assert encoding["y"]["field"] == "s" and encoding["color"]["field"] == "cat"  # flipped horizontal: the label axis is y


def test_an_empty_result_is_refused_rather_than_drawn():
    with pytest.raises(ValueError, match="empty result"):
        build(bar(), result(["cat", "n"], []), "db")


def test_a_duration_is_shown_at_the_magnitude_a_reader_uses():
    """Nobody expresses a song as 5,286,953 milliseconds. The column name carries the unit, so the renderer can rescale it."""
    tracks = result(["track", "Milliseconds"], [[f"t{i}", 5286953 - i * 40000] for i in range(10)])
    vega_lite = build(bar(x="track", y="Milliseconds"), tracks, "chinook")
    assert vega_lite["encoding"]["x"]["title"] == "Minutes"
    assert max(row["Milliseconds"] for row in vega_lite["data"]["values"]) == pytest.approx(88.1, abs=0.1)


def test_a_count_axis_has_no_fractional_ticks():
    """Every value is 1 or 2 and the axis used to offer 1.5 customers."""
    cities = result(["city", "customer_count"], [[f"city {i}", 1 + i % 2] for i in range(10)])
    assert build(bar(x="city", y="customer_count"), cities, "chinook")["encoding"]["x"]["axis"] == {"format": ",d", "values": [0, 1, 2]}


def test_a_large_count_is_abbreviated_rather_than_spelled_out():
    wide = result(["cat", "n"], [[f"c{i}", 5_286_953 - i] for i in range(5)])
    assert panel(build(bar(), wide, "db"))["encoding"]["y"]["axis"]["format"] == "~s"  # 5.3M, not 5,286,953


def test_a_month_axis_labels_the_months_it_holds():
    """Twelve points under six ticks, the first of them labelled 2009: the labels no longer line up with the dots."""
    axis = build(line(x="month", y="revenue"), result(["month", "revenue"], months(12), intent="trend"), "chinook")["encoding"]["x"]["axis"]
    assert axis == {"format": "%b", "tickCount": {"interval": "month", "step": 1}, "labelOverlap": False}


def test_a_long_month_axis_labels_every_kth_month():
    axis = build(line(x="month", y="revenue"), result(["month", "revenue"], months(60), intent="trend"), "chinook")["encoding"]["x"]["axis"]
    assert axis["format"] == "%b %Y" and axis["tickCount"]["step"] == 6  # eight-character labels, so every sixth fits


def test_a_zoomed_measure_axis_says_so_on_the_chart():
    """A trend line may zoom to its range, which is the house rule; the reader who cannot see zero is told it is not there."""
    subtitle = build(line(x="month", y="revenue"), result(["month", "revenue"], months(12), intent="trend"), "chinook")["title"]["subtitle"]
    assert any("does not start at zero" in line for line in subtitle)
    assert build(bar(), result(["cat", "n"], rows(3)), "db")["encoding"]["y"]["scale"]["zero"] is True  # a bar never zooms


def test_named_buckets_keep_their_own_order():
    """'<60 min' before '60-89 min': a bucket is ordinal even when it is text, so the magnitude sort does not touch it."""
    buckets = result(["bucket", "films"], [["150+ min", 250], ["60-89 min", 224], ["120-149 min", 216], ["<60 min", 96]], intent="distribution")
    assert build(bar(x="bucket", y="films"), buckets, "sakila")["encoding"]["x"]["sort"] == ["<60 min", "60-89 min", "120-149 min", "150+ min"]


def test_raw_values_are_bucketed_by_the_renderer():
    """Two hundred films each with its own bar is not a distribution; the buckets are the renderer's, so they are even and ordered."""
    films = result(["title", "length"], [[f"film {i}", 40 + (i * 7) % 140] for i in range(200)], intent="distribution")
    encoding = build(bar(x="title", y="length"), films, "sakila")["encoding"]
    assert encoding["x"]["bin"] == {"maxbins": 10} and encoding["y"]["aggregate"] == "count"


def test_a_column_with_few_distinct_values_is_not_binned():
    """Draco: a column with fewer than fifteen distinct values has its values as the buckets already."""
    scores = result(["film", "score"], [[f"f{i}", i % 8] for i in range(60)], intent="distribution")
    encoding = build(ChartSpec(chart_type="histogram", x="film", y="score", title="t"), scores, "sakila")["encoding"]
    assert "bin" not in encoding["x"] and encoding["x"]["type"] == "ordinal"


def test_two_measures_become_a_scatter():
    lengths = result(["length", "rentals"], [[40 + i, 3 + (i * 3) % 20] for i in range(50)])
    vega_lite = build(bar(x="length", y="rentals"), lengths, "sakila")
    assert vega_lite["mark"]["type"] == "point" and vega_lite["encoding"]["x"]["title"] == "length"  # both axes are measures, so both are titled


def test_a_dense_grid_becomes_a_heatmap():
    """Twenty-four countries by five years is 120 grouped bars six pixels wide. The same numbers as cells are readable."""
    grid = result(["country", "year", "revenue"], [[f"country {c}", f"201{y}", c + y] for c in range(24) for y in range(5)])
    vega_lite = build(bar(x="country", y="revenue", color="year"), grid, "chinook")
    assert vega_lite["mark"]["type"] == "rect"
    assert vega_lite["encoding"]["y"]["field"] == "country" and vega_lite["encoding"]["color"]["field"] == "revenue"


def test_a_sparse_grid_stays_bars():
    """Most of the cells would be empty, and an empty cell says less than a bar that is simply not there."""
    sparse = result(["rating", "category", "films"], [[f"r{i % 5}", f"cat{i}", 10 + i] for i in range(12)])
    assert build(bar(x="rating", y="films", color="category"), sparse, "sakila")["mark"]["type"] == "bar"


def test_a_histogram_of_an_already_bucketed_table_is_a_bar_chart():
    """Five rows of counts have no spread to bin: asked for a histogram, they would be five bars of height one."""
    buckets = result(["rating", "films"], [["G", 178], ["PG", 194], ["R", 195]], intent="distribution")
    encoding = panel(build(ChartSpec(chart_type="histogram", x="rating", y="films", title="t"), buckets, "sakila"))["encoding"]
    assert encoding["y"]["field"] == "films" and "aggregate" not in encoding["y"]


def test_bars_too_close_to_tell_apart_carry_their_numbers():
    """The ten longest tracks are 88, 84, and then eight of 48-and-a-bit minutes: the near-equality is real, and length alone
    cannot show a reader that it is near rather than exact."""
    tracks = result(["track", "Milliseconds"], [[f"t{i}", 5286953 if i == 0 else 2960293 - i * 900] for i in range(10)])
    layered = build(bar(x="track", y="Milliseconds"), tracks, "chinook")
    assert layered["layer"][1]["mark"]["type"] == "text" and layered["layer"][1]["encoding"]["text"]["field"] == "Milliseconds"
    spread = result(["cat", "n"], [["A", 10], ["B", 50], ["C", 90]])
    assert "layer" not in build(bar(), spread, "db")  # bars a reader can tell apart are not turned into a table


def test_an_unbucketed_date_column_is_a_time_axis():
    """A trend over '2017-09-08 19:16:31' used to type as nominal, so the line became a bar over unordered categories."""
    arrivals = result(["arrived", "dogs"], [[f"2017-09-{d:02d} 19:16:31", d] for d in range(1, 16)], intent="trend")
    vega_lite = build(line(x="arrived", y="dogs"), arrivals, "dog_kennels")
    assert panel(vega_lite)["mark"]["type"] == "line" and panel(vega_lite)["encoding"]["x"]["type"] == "temporal"


def test_a_heatmap_over_two_measures_is_not_a_heatmap():
    """Found live on the benchmark: asked for a correlation "in a scatter chart", the model returned a heatmap of two numeric
    columns. A grid of cells needs two categorical axes, so this one falls back to the marks those columns can take."""
    correlation = result(["Credits", "DNO"], [[1 + i % 4, 50 * (i % 12)] for i in range(76)])
    vega_lite = build(ChartSpec(chart_type="heatmap", x="Credits", y="DNO", color="Credits", title="t"), correlation, "college_3")
    assert panel(vega_lite)["mark"]["type"] != "rect"


# A pie at two or three parts, and the stacking the Visualization Agent now asks for directly (Sunday).

def _built(spec, rows, columns, intent="comparison"):
    from data_agents.charts import renderer
    from data_agents.contracts import AnalysisResult, QueryResult
    result = AnalysisResult(intent=intent, sql="", narrative="", table=QueryResult(columns=columns, rows=rows))
    return renderer.build(spec, result, "probe")


def test_a_pie_is_drawn_at_three_parts_and_refused_at_seven():
    """The House Style refuses a pie because it reads worse than bars past two or three parts; below that line its own
    justification does not object, and refusing anyway was the style disagreeing with itself."""
    from data_agents.charts import chartable
    from data_agents.contracts import ChartSpec
    spec = ChartSpec(chart_type="pie", x="part", y="n", title="t")
    three = _built(spec, [["a", 5], ["b", 3], ["c", 2]], ["part", "n"])
    assert chartable.panel(three)["mark"]["type"] == "arc" and chartable.unreadable(three) is None
    seven = _built(spec, [[c, 1] for c in "abcdefg"], ["part", "n"])
    assert chartable.panel(seven)["mark"]["type"] == "bar"  # past the line the refusal stands and the parts become bars


def test_a_pie_needs_parts_that_sum_to_a_whole():
    from data_agents.charts import chartable
    from data_agents.contracts import ChartSpec
    spec = ChartSpec(chart_type="pie", x="part", y="n", title="t")
    negative = _built(spec, [["a", 5], ["b", -3]], ["part", "n"])
    assert chartable.panel(negative)["mark"]["type"] == "bar"  # a negative slice has no angle


def test_bars_stack_when_the_spec_says_the_parts_sum_to_the_whole():
    """One multi-series bar in 34 stacked while `intent == 'share'` was the only way to ask, so the spec now says it."""
    from data_agents.charts import chartable
    from data_agents.contracts import ChartSpec
    rows = [["q1", "new", 5], ["q1", "repeat", 3], ["q2", "new", 6], ["q2", "repeat", 4]]
    grouped = _built(ChartSpec(chart_type="bar", x="quarter", y="n", color="kind", title="t"), rows, ["quarter", "kind", "n"])
    stacked = _built(ChartSpec(chart_type="bar", x="quarter", y="n", color="kind", title="t", stacked=True), rows, ["quarter", "kind", "n"])
    assert "xOffset" in chartable.panel(grouped)["encoding"]      # side by side: every bar reads against zero
    assert "xOffset" not in chartable.panel(stacked)["encoding"]  # stacked: the whole is the finding
    assert "color" in chartable.panel(stacked)["encoding"]


def test_labels_that_fit_only_slanted_are_turned_where_the_axis_cannot_flip():
    """Slanting instead of measuring the rendered text: a bar flips, but a heatmap's columns and a scatter's categories have no
    other axis to move to. Eight thirteen-character labels in 80-pixel slots overlap upright (89 px each) and fit at 45 degrees
    (71 px), so they turn, and `unreadable` judges the turned width."""
    grid = result(["cat", "s", "n"], [[f"c{c}", f"series-{i:06d}", c + i] for c in range(10) for i in range(8)])
    spec = build(bar(color="s"), grid, "db")
    assert panel(spec)["mark"]["type"] == "rect" and panel(spec)["encoding"]["x"]["axis"] == {"labelAngle": -45} and unreadable(spec) is None
    wider = result(["cat", "s", "n"], [[f"c{c}", f"series-{i:010d}", c + i] for c in range(10) for i in range(8)])
    assert "do not fit" in unreadable(build(bar(color="s"), wider, "db"))  # seventeen characters do not fit even turned


def test_a_ranking_asked_over_a_time_axis_draws_the_dates_as_ranked_categories():
    """24 of the free sweep's 28 remaining order misses were a bar over dates with a measure order asked. The dates become
    categories in the order asked; a line keeps its timeline and drops the order, and past MAX_BARS the ranking would cut rows."""
    days = result(["day", "sales"], [[f"2019-03-{d:02d}", (d * 7) % 23] for d in range(1, 11)], intent="trend")
    ranked = build(bar(x="day", y="sales", sort="measure desc"), days, "db")
    label = panel(ranked)["encoding"]["y"]  # ten dates flip horizontal like any ten categories
    assert label["type"] == "nominal" and label["sort"] == "-x" and unreadable(ranked) is None
    timeline = build(line(x="day", y="sales", sort="measure desc"), days, "db")
    assert panel(timeline)["encoding"]["x"]["type"] == "temporal"
    many = result(["day", "sales"], [[f"2019-{1 + d // 28:02d}-{1 + d % 28:02d}", d % 23] for d in range(60)], intent="trend")
    assert panel(build(bar(x="day", y="sales", sort="measure desc"), many, "db"))["encoding"]["x"]["type"] == "temporal"


def test_past_the_bar_cap_a_grouped_bar_keeps_whole_labels_not_the_largest_rows():
    """Cut by rows, some labels kept one series and lost the other, which reads as a zero that is not there."""
    two_series = result(["cat", "s", "n"], [[f"c{c}", s, c + (10 if s == "a" else 0)] for c in range(50) for s in ("a", "b")])
    vega_lite = build(bar(color="s"), two_series, "db")
    drawn = vega_lite["data"]["values"]
    assert len({r["cat"] for r in drawn}) == MAX_BARS and len(drawn) == 2 * MAX_BARS  # every kept label has both series
    assert unreadable(vega_lite) is None
