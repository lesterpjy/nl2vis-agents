"""Deterministic renderer: ChartSpec + result table -> Vega-Lite spec in the House Style. No model, no generated code.

`form` decides what the chart is, `presentation` decides what its numbers say, and this composes both into one validated spec.
Corrections only; what cannot be corrected is left for `chartable.unreadable` to name.
"""

import hashlib
from pathlib import Path

import altair as alt

from data_agents.charts import form, house_style, presentation
from data_agents.contracts import AnalysisResult, ChartSpec
from data_agents.charts.form import field_type

# What a drawn chart depends on besides its Chart Spec and its rows: this code and Altair. A Vega-Lite kept under another
# fingerprint is redrawn from its spec, so a House Style or form change restyles history on purpose, and is seen to.
FINGERPRINT = hashlib.sha1(b"".join(Path(m.__file__).read_bytes() for m in (form, house_style, presentation)) + Path(__file__).read_bytes()
                           + alt.__version__.encode()).hexdigest()[:12]

# A Sort as Vega spells it on the label channel, upright and flipped: on a horizontal bar the measure is the x channel, so the
# same request is spelled against the other axis. A label axis left unsorted is already ascending by label.
SORTS = {"measure desc": ("-y", "-x"), "measure asc": ("y", "x"), "category asc": ("ascending", "ascending"), "category desc": ("descending", "descending")}


def build(spec: ChartSpec, result: AnalysisResult, database: str) -> dict:
    """Vega-Lite spec, validated against the schema by Altair, with the House Style config attached last."""
    columns = result.table.columns
    for column in (spec.x, spec.y, spec.color):
        if column is not None and column not in columns:
            raise ValueError(f"column {column!r} is not in the result ({', '.join(columns)})")
    if not result.table.rows:
        raise ValueError("an empty result has nothing to chart")
    data = [dict(zip(columns, row)) for row in result.table.rows]
    types = {c: field_type([r[c] for r in data]) for c in columns}
    if types[spec.y] != "quantitative":
        raise ValueError(f"y column {spec.y!r} is not numeric")
    if spec.x == spec.y:  # the invariant's channel half: one column cannot be compared with itself (chartable)
        raise ValueError(f"column {spec.x!r} is named for both x and y")

    shape = form.choose(spec, result.intent, data, types, result.table.truncated)
    if shape.categorical:
        types = types | {shape.x: "nominal"}  # a ranking over dates: drawn as the categories the form layer decided
    measure_column = shape.color if shape.mark == "rect" else shape.y
    measure = presentation.measure(measure_column, [r[measure_column] for r in shape.data])
    data = _rescale(shape.data, measure_column, measure.factor)
    # A numeric category axis is a second measure (a scatter's x), and a measure axis with no title is a row of bare numbers.
    across = presentation.measure(shape.x, [r[shape.x] for r in data]) if types[shape.x] == "quantitative" and shape.mark != "rect" and not shape.counted else None
    if across:
        data = _rescale(data, shape.x, across.factor)
    zero = shape.mark not in ("line", "point", "arc")
    if not zero and (note := presentation.zoom_note([r[measure_column] for r in data])):
        shape.notes.append(note)
    labelled = shape.mark == "bar" and not shape.color and not shape.counted and presentation.indistinct(
        [r[shape.y] for r in data], house_style.WIDTH if shape.horizontal else shape.height, zero)

    time_x = shape.x if types[shape.x] == "temporal" and shape.mark != "rect" and not shape.counted else None
    discrete = ({shape.x, shape.y} if shape.mark == "rect" else {shape.x, shape.color}) - {None, measure_column, time_x}
    parse = {c: "string" for c in columns if types[c] == "nominal" or c in discrete}  # otherwise Vega guesses, and "2012" becomes a date
    ticks = presentation.time_axis([r[time_x] for r in data], house_style.WIDTH) if time_x else None
    if ticks:
        parse[time_x] = ticks.parse
    chart = alt.Chart(alt.Data(values=data, format=alt.DataFormat(parse=parse)))

    if shape.mark == "arc":
        chart = _pie(chart, shape, measure, columns, types)
    elif shape.mark == "rect":
        chart = _heatmap(chart, shape, measure, columns, types)
    elif shape.counted:
        chart = _histogram(chart, shape, measure)
    else:
        chart = _cartesian(chart, shape, measure, across, ticks, zero, labelled, columns, types, time_x)
    subtitle = [line for line in [spec.subtitle, *spec.annotations[:2], *shape.notes] if line] + [house_style.caption(database, result.sql)]
    title = alt.Title(spec.title, subtitle=subtitle)
    vega_lite = chart.properties(width=house_style.WIDTH, height=shape.height, title=title).to_dict()  # schema-validated here
    vega_lite["config"] = house_style.CONFIG
    return vega_lite


def _cartesian(chart, shape: form.Form, measure: presentation.Measure, across, ticks, zero: bool, labelled: bool, columns, types, time_x):
    axis = alt.Axis(format=ticks.format, tickCount={"interval": ticks.interval, "step": ticks.step}, labelOverlap=False) if ticks else (_ticks(across) if across else alt.Undefined)
    sort = shape.sort if isinstance(shape.sort, list) else SORTS.get(shape.sort, (alt.Undefined, alt.Undefined))[shape.horizontal]
    title = across.title if across else None
    if not shape.horizontal and types[shape.x] == "nominal" and not presentation.labels_fit({r[shape.x] for r in shape.data}, house_style.WIDTH):
        axis = alt.Axis(labelAngle=presentation.SLANT)  # cannot flip here, so the labels turn; `unreadable` judges the slanted width
    label = alt.X(field=shape.x, type=types[shape.x], title=title, axis=axis, sort=sort)
    value = alt.Y(field=shape.y, type="quantitative", title=measure.title, scale=alt.Scale(zero=zero), axis=_ticks(measure))
    if shape.horizontal:  # the label axis is the one that can grow, so the roles of the two channels swap
        label = alt.Y(field=shape.x, type=types[shape.x], title=title, axis=axis, sort=sort)
        value = alt.X(field=shape.y, type="quantitative", title=measure.title, scale=alt.Scale(zero=zero), axis=_ticks(measure))
    tooltip = [alt.Tooltip(field=c, type=types[c] if c == time_x or types[c] != "temporal" else "nominal") for c in columns]
    across_axis, down_axis = (value, label) if shape.horizontal else (label, value)
    marked = getattr(chart, f"mark_{shape.mark}")().encode(x=across_axis, y=down_axis, tooltip=tooltip)
    if labelled:
        # Bars this close read as equal, so their numbers are printed beside them. Never a default: a value on every bar is a table.
        text = chart.mark_text(align="left" if shape.horizontal else "center", dx=4 if shape.horizontal else 0, dy=0 if shape.horizontal else -6).encode(
            x=across_axis, y=down_axis, text=alt.Text(field=shape.y, type="quantitative", format=measure.format or house_style.CONFIG["numberFormat"]))
        return alt.layer(marked, text)
    chart = marked
    if shape.color:
        chart = chart.encode(color=alt.Color(field=shape.color, type="nominal", title=None))  # series are discrete, even years
        if shape.grouped:  # several rows per category: side by side, so every bar reads against zero
            offset = alt.YOffset(field=shape.color, type="nominal") if shape.horizontal else alt.XOffset(field=shape.color, type="nominal")
            chart = chart.encode(yOffset=offset) if shape.horizontal else chart.encode(xOffset=offset)
    return chart


def _pie(chart, shape: form.Form, measure: presentation.Measure, columns, types):
    """Parts of a whole at two or three parts, where angle is still read accurately and the shares are the finding.

    The slices carry the palette in the order the form layer sorted them (largest first), and each is separated by a stroke in
    the surface colour so neighbouring wedges do not merge.
    """
    order = [r[shape.x] for r in shape.data]
    # Vega's default radius is half the shorter side, which is exactly the view height, so the circle meets the edge and the
    # bottom is clipped. The radius is set here instead, with room left for the stroke.
    return chart.mark_arc(stroke=house_style.SURFACE, strokeWidth=2, outerRadius=shape.height // 2 - 8).encode(
        theta=alt.Theta(field=shape.y, type="quantitative", stack=True),
        color=alt.Color(field=shape.x, type="nominal", title=None, sort=order),
        order=alt.Order(field=shape.y, type="quantitative", sort="descending"),
        tooltip=[alt.Tooltip(field=c, type=types[c] if types[c] != "temporal" else "nominal",
                             format=measure.format if c == shape.y and measure.format else alt.Undefined) for c in columns])


def _heatmap(chart, shape: form.Form, measure: presentation.Measure, columns, types):
    """Both axes are categories and the measure is the cell's colour, so the palette carries magnitude instead of identity."""
    order = alt.EncodingSortField(field=shape.color, op="sum", order="descending")
    columns_of_cells = {r[shape.x] for r in shape.data}
    slant = alt.Axis(labelAngle=presentation.SLANT) if not presentation.labels_fit(columns_of_cells, house_style.WIDTH) else alt.Undefined
    return chart.mark_rect(stroke=house_style.SURFACE, strokeWidth=1).encode(
        x=alt.X(field=shape.x, type="nominal", title=None, axis=slant),
        y=alt.Y(field=shape.y, type="nominal", title=None, sort=order),
        color=alt.Color(field=shape.color, type="quantitative", title=measure.title, legend=alt.Legend(format=measure.format) if measure.format else alt.Undefined),
        tooltip=[alt.Tooltip(field=c, type="nominal" if types[c] != "quantitative" else "quantitative") for c in columns])


def _histogram(chart, shape: form.Form, measure: presentation.Measure):
    """The spread of one measure: the renderer cuts the buckets, so they are ordered, evenly wide and countable."""
    bucket = dict(field=shape.y, title=measure.title, **({"type": "quantitative", "bin": alt.Bin(maxbins=form.BINS)} if shape.binned else {"type": "ordinal"}))
    count = alt.Y(aggregate="count", type="quantitative", title="Rows", axis=alt.Axis(format=",d", tickMinStep=1))  # whole, and its range is unknown here
    axis = _ticks(measure) if shape.binned else (alt.Axis(labelAngle=presentation.SLANT) if not presentation.labels_fit({r[shape.y] for r in shape.data}, house_style.WIDTH) else alt.Undefined)
    return chart.mark_bar().encode(x=alt.X(**bucket, axis=axis), y=count,
                                   tooltip=[alt.Tooltip(**bucket), alt.Tooltip(aggregate="count", type="quantitative", title="Rows")])


def _ticks(measure: presentation.Measure):
    ticks = {k: v for k, v in (("format", measure.format), ("values", measure.ticks)) if v is not None}
    return alt.Axis(**ticks) if ticks else alt.Undefined


def _rescale(data: list[dict], column: str, factor: float) -> list[dict]:
    if factor == 1.0:
        return data
    return [row | {column: row[column] / factor if isinstance(row[column], (int, float)) else row[column]} for row in data]
