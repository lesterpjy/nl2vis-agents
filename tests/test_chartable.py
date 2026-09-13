"""Tier 1: the chartable-shape rule and the readability rule, table-driven, no model."""

import pytest

from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from data_agents.charts.chartable import no_measure, split_identity, unreadable
from data_agents.charts.renderer import build

ACTORS = [["GINA", "DEGENERES", 42], ["WALTER", "TORN", 41], ["MARY", "KEITEL", 40]]


@pytest.mark.parametrize("columns,rows,expected", [
    (["actor", "films"], [["A", 3], ["B", 2], ["C", 1]], []),                                    # one label column: chartable
    (["first_name", "last_name", "films"], ACTORS, ["first_name", "last_name"]),                 # the actors failure: the name is split
    (["city", "country", "orders"], [["Bern", "CH", 3], ["Rome", "IT", 2], ["Oslo", "NO", 1]], ["city", "country"]),
    (["category", "year", "revenue"], [["A", "2011", 1], ["A", "2012", 2], ["B", "2011", 3]], []),  # a series column repeats: not a split identity
    (["title", "rating", "rentals"], [["A", "PG", 3], ["B", "R", 2], ["C", "PG", 1]], []),       # rating repeats, so the title alone labels a row
    (["year", "artist", "plays"], [["2009", "A", 1], ["2010", "B", 2], ["2011", "C", 3]], []),   # a temporal column is an axis, never a name fragment
    (["first_name", "last_name", "films"], ACTORS[:2], []),                                      # under three rows, unique is coincidence
    # A city whose country repeats is a series column, not half a name: one bar per city, coloured by country, reads.
    (["city", "country", "orders"], [["Paris", "France", 3], ["Lyon", "France", 2], ["Rome", "Italy", 1]], []),
    # Prose is payload, not a name; combining it into the axis label would be worse than the split it looks like.
    (["title", "description", "rentals"], [["ACE GOLDFINGER", "A Astounding Epistle of a Database Administrator and a Explorer", 3],
                                           ["AIRPLANE SIERRA", "A Touching Saga of a Hunter And a Butler who must Discover a Car", 2],
                                           ["ALABAMA DEVIL", "A Thoughtful Panorama of a Database Administrator And a Mad Cow", 1]], []),
])
def test_split_identity(columns, rows, expected):
    assert split_identity(QueryResult(columns=columns, rows=rows)) == expected


@pytest.mark.parametrize("columns,rows,expected", [
    (["rating", "films"], [["PG", 194], ["R", 195]], False),
    (["name"], [["ACTION"], ["COMEDY"]], True),                        # held-out case 1434: a correct list, and nothing to draw
    (["first_name", "last_name"], [["GINA", "DEGENERES"], ["WALTER", "TORN"]], True),
    (["rating", "share"], [["PG", 19.4], ["R", None]], False),           # a null beside numbers is still a measure
    (["rating", "n"], [["PG", None], ["R", None]], True),                # all null is not
    (["year"], [["2012"], ["2013"]], True),                              # a date axis measures nothing
    (["rating", "films"], [], False),                                    # empty is the zero-row check's business
])
def test_no_measure(columns, rows, expected):
    assert no_measure(QueryResult(columns=columns, rows=rows)) is expected


VERTICAL = {"x": {"field": "actor", "type": "nominal"}, "y": {"field": "films", "type": "quantitative"}}
HORIZONTAL = {"y": {"field": "actor", "type": "nominal"}, "x": {"field": "films", "type": "quantitative"}}
ROWS = [{"actor": "A", "films": 3, "last": "X", "year": "2011"}, {"actor": "A", "films": 2, "last": "Y", "year": "2012"},
        {"actor": "B", "films": 1, "last": "Z", "year": "2011"}]
MANY = [{"actor": f"a{i}", "films": i, "series": f"s{i}"} for i in range(30)]


def series(n: int) -> list[dict]:
    """n series drawn across two categories, so no series is unique to a row."""
    return [{"actor": c, "films": i + 1, "series": f"s{i}"} for c in "AB" for i in range(n)]


@pytest.mark.parametrize("encoding,rows,height,reads", [
    (VERTICAL, ROWS[1:], 360, True),                                                          # two bars, two labels, no legend
    (VERTICAL | {"color": {"field": "last"}}, ROWS[1:], 360, False),                            # a legend with one entry per bar says nothing
    (VERTICAL | {"color": {"field": "year"}, "xOffset": {"field": "year"}}, ROWS[1:], 360, False),  # slots where every label appears once
    (VERTICAL | {"color": {"field": "year"}, "xOffset": {"field": "year"}}, ROWS, 360, True),   # a real series: A appears twice
    (VERTICAL, ROWS, 360, False),                                                              # A twice with nothing to tell the bars apart
    (VERTICAL | {"color": {"field": "series"}}, series(7), 360, False),                         # seven series, six colours: two of them match
    (VERTICAL | {"color": {"field": "series"}}, series(6), 360, True),                          # six is what the palette holds
    (HORIZONTAL, MANY, 360, False),                                                            # thirty bars in 360 pixels: the labels collide
    (HORIZONTAL, MANY, 30 * 22, True),                                                         # the same bars, given the height they need
    ({"x": {"field": "films", "type": "quantitative"}, "y": {"field": "runtime", "type": "quantitative"}}, ROWS, 360, True),  # no label axis
])
def test_unreadable(encoding, rows, height, reads):
    chart = {"encoding": encoding, "data": {"values": rows}, "height": height, "width": 640, "mark": {"type": "bar"}}
    assert (unreadable(chart) is None) == reads


def test_the_renderer_builds_a_chart_that_reads():
    """The colour and offset rules in build are what unreadable checks for; this is the link between them."""
    result = AnalysisResult(intent="comparison", sql="s", narrative="n",
                            table=QueryResult(columns=["first_name", "last_name", "films"], rows=ACTORS))
    spec = ChartSpec(chart_type="bar", x="first_name", y="films", color="last_name", title="t")
    assert unreadable(build(spec, result, "sakila")) is None
