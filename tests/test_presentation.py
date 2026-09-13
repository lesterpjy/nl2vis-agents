"""Tier 1: what a chart's numbers say. Table-driven, no model, no rendering."""

import pytest

from data_agents.charts.presentation import bucket_order, labels_fit, measure, tick_step, time_axis, zoom_note


@pytest.mark.parametrize("column,values,title,factor", [
    ("Milliseconds", [5286953, 4900000], "Minutes", 60_000),          # a song is 88 minutes long, never 5,286,953 of anything
    ("track_ms", [90, 990], "track (milliseconds)", 1),                # already small: the stored unit is the readable one
    ("track_ms", [900, 1200], "track (seconds)", 1000),                # and it moves up a rung as soon as the numbers run past three digits
    ("run_seconds", [7200, 9000], "run (minutes)", 60),   # 150 minutes: the smallest rung that still reads in three digits
    ("Bytes", [5242880, 1048576], "Megabytes", 1024**2),
    ("track_share_percent", [86.6, 6.9], "track share (%)", 1),
    ("customer_count", [1, 2], "customer count", 1),                  # no unit in the name, so none is invented
    ("length", [40, 180], "length", 1),                               # Sakila stores minutes here and the name does not say so
    ("OrdersHandled", [3, 2], "Orders Handled", 1),
    ("min_price", [3, 9], "min price", 1),                             # "min" in front is a minimum: measured over the stored runs, never minutes
    ("Min_Age", [18, 65], "Min Age", 1),
    ("length_min", [40, 180], "length (minutes)", 1),                  # and at the end it is the unit
])
def test_the_unit_comes_from_the_column_name_and_the_magnitude_from_the_values(column, values, title, factor):
    found = measure(column, values)
    assert (found.title, found.factor) == (title, factor)


@pytest.mark.parametrize("column,values,fmt,ticks", [
    ("customer_count", [1, 2], ",d", [0, 1, 2]),   # a count has no half, so the axis has no 1.5
    ("film_count", [250, 96], ",d", None),         # whole, but too many to place by hand: Vega's own ticks are integers here
    ("n", [5_286_953, 1], "~s", None),             # 5.3M, because "5,286,953" is nine characters of tick label
    ("revenue", [35.64, 37.62], None, None),       # fractional: the House Style's own number format
    ("Milliseconds", [5286953], None, None),       # rescaled, so no longer whole numbers
])
def test_the_tick_format_follows_the_numbers(column, values, fmt, ticks):
    found = measure(column, values)
    assert (found.format, found.ticks) == (fmt, ticks)


@pytest.mark.parametrize("values,fmt,interval,step", [
    ([f"2009-{m:02d}" for m in range(1, 13)], "%b", "month", 1),                       # twelve months, twelve labels
    ([f"{2009 + m // 12}-{m % 12 + 1:02d}" for m in range(60)], "%b %Y", "month", 6),  # the year has to be said, so fewer fit
    (["2009", "2010", "2011"], "%Y", "year", 1),
    ([f"2009-06-{d:02d}" for d in range(1, 31)], "%-d %b", "day", 3),
    ([f"2017-09-{d:02d} 19:16:31" for d in range(1, 16)], "%-d %b", "day", 2),  # a date column nobody bucketed; fifteen labels, every second one fits
])
def test_ticks_land_on_the_buckets_the_data_holds(values, fmt, interval, step):
    axis = time_axis(values, 640)
    assert (axis.format, axis.interval, axis.step) == (fmt, interval, step)


@pytest.mark.parametrize("labels,expected", [
    (["150+ min", "60-89 min", "<60 min", "120-149 min"], ["<60 min", "60-89 min", "120-149 min", "150+ min"]),
    (["1-5", "6-10", "11-20"], ["1-5", "6-10", "11-20"]),
    (["Drama", "Comedy"], None),          # ordinary categories, sorted by magnitude like any other
    (["2009", "Comedy"], None),
])
def test_buckets_are_ordinal_even_when_they_are_text(labels, expected):
    assert bucket_order(labels) == expected


@pytest.mark.parametrize("labels,width,fits", [
    (["Jan", "Feb", "Mar"], 640, True),
    ([f"city {i}" for i in range(5)], 640, True),
    (["Protected AAC audio file", "Protected MPEG-4 video file", "MPEG audio file", "AAC audio file", "Purchased AAC audio file"], 640, False),
    ([], 640, True),
])
def test_a_label_has_to_fit_its_own_slot_not_the_whole_axis(labels, width, fits):
    assert labels_fit(labels, width) is fits


@pytest.mark.parametrize("values,noted", [
    ([35.64, 37.62], True),    # a 5% spread drawn full height: the reader has to be told where the axis starts
    ([2, 100], False),         # the drawn part is larger than the hidden part; Vega will include zero anyway
    ([0, 100], False),
    ([5, 5], False),           # no spread at all
])
def test_a_zoomed_axis_is_named_when_it_exaggerates(values, noted):
    assert (zoom_note(values) is not None) is noted


def test_tick_step_is_the_smallest_that_fits():
    assert tick_step(12, 3, 640) == 1 and tick_step(60, 8, 640) == 6 and tick_step(1, 40, 640) == 1
