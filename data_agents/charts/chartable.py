"""The chart invariant, stated once and enforced in code:

    A chart has a label channel and a measure channel. The label channel carries at least two distinct values, one per mark,
    or there is nothing to compare and the numbers say it better. One column never plays two channels, and a measure plays
    x only on a point chart.

Each half is enforced where the layer that can satisfy it sits. `split_identity` and `no_measure` run in `finish_analysis`,
because the Analysis Agent can add a COUNT(*) and the Visualization Agent cannot invent a column. `single_row` runs in
the orchestrator before any Visualization Agent call: a one-row result, 1×1 or 1×N, has no second mark to compare (the case
that shipped `TotalSales` on both axes of a bar). `two_channels` is refused by the Visualization Agent's validator and again by
`renderer.build`, which also drops a colour that repeats an axis. `unreadable` runs on the built Vega-Lite, after every
correction the renderer can make, so a chart that still cannot be read is skipped with its reason rather than shipped. The
shape grid (`tests/test_shape_grid.py`) crosses rows × label columns × measures × duplicated labels through the same path and
asserts exactly two outcomes: a chart that reads, or a skip with a reason.
"""

from data_agents.charts import house_style
from data_agents.contracts import ChartSpec, QueryResult
from data_agents.charts.form import BAND, MANY_CATEGORIES, MAX_SLICES, field_type
from data_agents.charts.presentation import CHAR_PX, SLANT, labels_fit, text_width

# What a user reads when no chart is drawn: one fixed sentence per reason code, never the technical text. The table
# is still the answer; the sentence says why it is the whole answer.
PLAIN = {
    "empty": "The result has no rows, so there is nothing to draw.",
    "single_value": "The answer is a single number, shown as it is rather than as a one-bar chart.",
    "unbuildable": "No column of this result can be drawn as a measure, so the table is the answer.",
    "unreadable": "A chart of these columns would not be readable, so the table is the answer.",
    "reshape": "This table cannot be charted as it stands and reshaping it did not help, so the table is the answer.",
}

MIN_ROWS = 3  # under this every text column is unique by accident, not because the row's identity is split
LABEL_CHARS = 40  # measured over the three databases: name columns average 5 to 32 characters, prose columns 94 and 265
LABEL_PX = LABEL_CHARS * CHAR_PX  # as width, so forty CJK characters read as the paragraph they are, not as a name


def names_every_row(values: list) -> bool:
    """A name a reader could put under a bar, and no two rows share it. Prose (a film's description) is payload, not a name."""
    return (field_type(values) == "nominal" and len(set(values)) == len(values)
            and sum(text_width(v) for v in values) / len(values) <= LABEL_PX)


def split_identity(table: QueryResult) -> list[str]:
    """The text columns that each name every row. More than one means the identity is split across columns
    (a first and last name; a city and a country that each appear once), so no single column can label a bar and the rest
    get misused as colour. A column that repeats is a series the chart can carry as colour, so it is not a fragment."""
    if len(table.rows) < MIN_ROWS:
        return []
    columns = [c for i, c in enumerate(table.columns) if names_every_row([row[i] for row in table.rows])]
    return columns if len(columns) > 1 else []


def single_row(table: QueryResult) -> str | None:
    """A one-row result printed as its numbers: with one mark there is nothing to compare, whatever the columns. The string
    is what the user reads in the chart's place: the bare number for a 1×1, each measure named for a 1×N."""
    if len(table.rows) != 1:
        return None
    cells = [(c, v) for c, v in zip(table.columns, table.rows[0]) if field_type([v]) == "quantitative"]
    if len(table.columns) == 1 and cells:
        return f"{cells[0][1]:,}"
    return ", ".join(f"{c} {v:,}" for c, v in cells) or ", ".join(str(v) for v in table.rows[0])


def two_channels(spec: ChartSpec) -> str | None:
    """One column named for two channels: the chart would compare a column with itself."""
    if spec.x == spec.y:
        return f"{spec.x!r} is named for both x and y"
    if spec.color in (spec.x, spec.y):
        return f"{spec.color!r} is named for both color and an axis"
    return None


def no_measure(table: QueryResult) -> bool:
    """No column measures the rows, so there is nothing to draw a bar's length from. Refused here because downstream it could
    only be refused three times and then error: a correct two-column list used to end a Turn with no answer at all."""
    return bool(table.rows) and not any(field_type([row[i] for row in table.rows]) == "quantitative" for i in range(len(table.columns)))


def panel(vega_lite: dict) -> dict:
    """The marks of a chart. Printing values beside indistinguishable bars layers the spec, so mark and encoding move down one."""
    return vega_lite["layer"][0] if "layer" in vega_lite else vega_lite


def unreadable(vega_lite: dict) -> str | None:
    """Why the built chart cannot be read, or None. Checked on the artifact, so it judges what the renderer actually drew."""
    marks = panel(vega_lite)
    encoding, rows, mark = marks["encoding"], vega_lite["data"]["values"], marks["mark"]["type"]
    if mark == "arc":  # no axes to overflow: a pie reads when its slices are few enough to tell apart by angle and by colour
        slices = {row[encoding["color"]["field"]] for row in rows}
        return (None if len(slices) <= MAX_SLICES else
                f"{len(slices)} slices is more than the {MAX_SLICES} a pie reads at")
    if mark == "rect":
        return _cells(vega_lite, encoding, rows)
    if "bin" in encoding.get("x", {}) or "aggregate" in encoding.get("y", {}):  # a histogram: the buckets are the renderer's own
        buckets = {row[encoding["x"]["field"]] for row in rows}
        return None if "bin" in encoding["x"] or labels_fit(buckets, vega_lite["width"], _slanted(encoding["x"])) else f"{len(buckets)} buckets do not fit the axis"
    label = next((e["field"] for e in (encoding.get("x"), encoding.get("y")) if e and e.get("type") == "nominal"), None)
    if series := encoding.get("color", {}).get("field"):
        values = {row[series] for row in rows}
        if len(values) > len(house_style.PALETTE):  # Vega recycles the palette, and two series in one colour is a lie
            return f"{len(values)} series on {series!r} is more than the {len(house_style.PALETTE)} colours the House Style has"
        if len(values) >= len(rows) and label is not None:  # beside a labelled axis a legend per row is noise; on a scatter it is the identity
            return f"the legend has one entry per row on {series!r}"
    if label is None:
        # A bar compares categories, so its category axis has to be categorical. A numeric one is an identifier or a range,
        # and neither labels a bar: with more values than a scale can show it draws a picket fence with a meaningless axis.
        if mark == "bar" and len({row[encoding["x"]["field"]] for row in rows}) > MANY_CATEGORIES:
            field = encoding["x"]["field"]
            names = [c for c in rows[0] if c != field and names_every_row([row[c] for row in rows])]
            return (f"{field!r} is a number, so the bars have no labels" +
                    (f"; {names[0]!r} names each row" if names else "; no column in the result names a row"))
        return None  # both axes are measures, which is what a point chart is for
    labels = [row[label] for row in rows]
    if {"xOffset", "yOffset"} & encoding.keys() and len(set(labels)) == len(labels):
        return f"bars sit in series slots but every {label!r} appears once"
    if not series and len(set(labels)) < len(labels) and "aggregate" not in encoding.get("y", {}):
        return f"{label!r} repeats on the category axis with nothing to tell those rows apart"
    if encoding.get("y", {}).get("field") == label:  # horizontal: the height grew to fit, so this is the renderer failing to
        if (band := vega_lite["height"] / len(set(labels))) < BAND:
            return f"{len(set(labels))} bars in {vega_lite['height']} pixels leaves {band:.0f} each, and the labels collide"
    # On the x axis the width is fixed and every label gets one slot of it, so a label too wide for its slot overlaps its
    # neighbour. The renderer flips a bar chart to avoid this; anything still here has to be said rather than drawn.
    elif not labels_fit(set(labels), vega_lite["width"], _slanted(encoding["x"])):
        return f"{len(set(labels))} labels on {label!r} do not fit the {vega_lite['width']} pixels the axis has"
    return None


def _slanted(channel: dict) -> bool:
    return isinstance(channel.get("axis"), dict) and channel["axis"].get("labelAngle") == SLANT


def _cells(vega_lite: dict, encoding: dict, rows: list[dict]) -> str | None:
    """A heatmap reads when both of its label axes read: one slot per column across a fixed width, one band per row down a height that grew."""
    columns = {row[encoding["x"]["field"]] for row in rows}
    bands = {row[encoding["y"]["field"]] for row in rows}
    if not labels_fit(columns, vega_lite["width"], _slanted(encoding["x"])):
        return f"{len(columns)} columns of cells do not fit the {vega_lite['width']} pixels the chart has"
    if (band := vega_lite["height"] / len(bands)) < BAND:
        return f"{len(bands)} rows of cells in {vega_lite['height']} pixels leaves {band:.0f} each, and the labels collide"
    return None
