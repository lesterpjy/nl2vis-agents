"""What VisEval's checks read, built from our Vega-Lite instead of a deconstructed SVG.

Their `chart_info` is the chart's name in their vocabulary, one datum per drawn row keyed `field_x`, `field_y` and
`field_<series>`, and a scale per channel. Two choices are ours, and they are the whole "differences" column of the protocol
table: the channels are the Chart Spec's roles, not the drawn axes, so a bar the renderer laid horizontally for room is the
upright chart transposed and its category is still x; and a channel is typed the way their deconstruction types a matplotlib
datum, a number, then a date, then a name, so a year drawn as a category still compares as a number.
"""

from data_agents.charts import chartable, presentation
from data_agents.contracts import AnalysisResult
from tests.bench import viseval_checks as published


def chart_info(vega_lite: dict, result: AnalysisResult) -> dict:
    panel = chartable.panel(vega_lite)
    mark, encoding, rows = panel["mark"]["type"], panel["encoding"], vega_lite["data"]["values"]
    if mark == "arc":
        label, measure = encoding["color"]["field"], encoding["theta"]["field"]
        factor = _factor(measure, result)
        return {"chart": "pie", "mark": "arc", "encoding": {"fill": {"type": "nominal"}, "theta": {"type": "quantitative"}},
                "data": [{"field_fill": str(r[label]), "field_theta": float(r[measure]) * factor} for r in rows if r[measure] is not None]}
    roles = _roles(mark, encoding)
    if roles["y"] is None:  # a histogram: the count is Vega's own, and no VisEval case asks for one
        return {"chart": "bar", "mark": mark, "encoding": {}, "data": []}
    fields: dict[str, str] = {channel: field for channel, field in roles.items() if field}
    series = next((channel for channel in ("fill", "stroke") if channel in fields), None)
    kinds = {channel: _kind([r[field] for r in rows]) for channel, field in fields.items()}
    factors = {channel: _factor(field, result) if kinds[channel] == "quantitative" else 1.0 for channel, field in fields.items()}
    data = [_datum(r, fields, kinds, factors) for r in rows if all(r[field] is not None for field in fields.values())]
    label = encoding["y"] if fields["x"] == encoding["y"].get("field") else encoding["x"]  # the channel that carries the sort
    # A label axis the renderer drew as categories (dates ranked by their measure) is read in its drawn order, which is
    # how their deconstruction reads a bar chart's ticks; the datum keeps the type their check gives its values.
    drawn = {e.get("field"): e.get("type") for e in encoding.values() if isinstance(e, dict)}
    scales = {channel: _scale(channel, "nominal" if drawn.get(field) == "nominal" else kinds[channel], data, label.get("sort")) for channel, field in fields.items()}
    stacked = mark == "bar" and series and not {"xOffset", "yOffset"} & encoding.keys()
    name = {"bar": "stacked bar" if stacked else "grouping bar", "line": "grouping line", "point": "grouping scatter"}.get(mark, mark) if series \
        else {"point": "scatter", "rect": "heatmap"}.get(mark, mark)
    return {"chart": name, "mark": mark, "encoding": {channel: {"type": kinds[channel], "scale": scales[channel]} for channel in fields}, "data": data}


def _roles(mark: str, encoding: dict) -> dict[str, str | None]:
    """Which result column each of VisEval's channels holds, in the spec's orientation. Their series channel is `fill` on a
    bar or a scatter and `stroke` on a line, which is how their deconstruction names it and how their line check reads it."""
    x, y = encoding["x"], encoding["y"]
    if mark == "rect":  # both axes are categories and the measure is the cell's colour
        return {"x": x["field"], "y": y["field"], "fill": encoding["color"]["field"]}
    if mark == "bar" and x.get("type") == "quantitative" and y.get("type") != "quantitative":
        x, y = y, x  # horizontal: the renderer swapped the channels for room, not the roles
    roles = {"x": x.get("field"), "y": y.get("field")}
    if color := encoding.get("color", {}).get("field"):
        roles["stroke" if mark == "line" else "fill"] = color
    return roles


def _kind(values: list) -> str:
    """Their typing of a datum, applied to ours: numeric first, then anything dateutil parses, then a name."""
    present = [str(v) for v in values if v is not None]
    if all(published.is_numeric(v) for v in present):
        return "quantitative"
    if all(published.is_datetime(v) for v in present):
        return "temporal"
    return "nominal"


def _datum(row: dict, fields: dict, kinds: dict, factors: dict) -> dict:
    datum = {}
    for channel, field in fields.items():
        value = row[field]
        if kinds[channel] == "quantitative":
            datum[f"field_{channel}"] = float(value) * factors[channel]
        else:
            datum[f"field_{channel}"] = str(value)
            if kinds[channel] == "temporal":  # their check compares a temporal channel on its timestamp
                datum[f"field_{channel}_origin"] = published.parse_time_to_timestamp(str(value))
    return datum


def _factor(column: str, result: AnalysisResult) -> float:
    """What the renderer divided the drawn numbers by (a measure in milliseconds drawn as minutes), undone so the data compares."""
    values = [row[result.table.columns.index(column)] for row in result.table.rows]
    return presentation.measure(column, values).factor


def _scale(channel: str, kind: str, data: list[dict], sort) -> dict:
    """Their order check reads a scale's domain against its range. A label channel's domain is the drawn order and its range
    the slot positions, counted up along x and down along y as a plotted axis runs; a measure's is a line."""
    values = [d[f"field_{channel}"] for d in data]
    if kind == "quantitative":
        return {"type": "linear", "domain": [min(values, default=0), max(values, default=0)], "range": [0, 1]}
    if kind == "temporal":
        domain = sorted(set(values), key=lambda v: published.parse_time_to_timestamp(v) or 0)
    else:
        domain = _drawn_order([(d[f"field_{channel}"], d.get("field_y", 0)) for d in data], sort)
    positions = list(range(1, len(domain) + 1))
    return {"domain": domain, "range": positions if channel == "x" else positions[::-1]}


def _drawn_order(pairs: list[tuple[str, float]], sort) -> list[str]:
    """The order Vega draws a label channel in, from its `sort`: an explicit list, by the measure's total, or by the label,
    ascending unless told otherwise, which is Vega's default and is why a spec with no sort is still ordered."""
    labels = list(dict.fromkeys(label for label, _ in pairs))
    if isinstance(sort, list):
        return [label for label in sort if label in labels] + [label for label in labels if label not in sort]
    if sort in ("x", "y", "-x", "-y"):
        totals = {label: sum(m for k, m in pairs if k == label and isinstance(m, (int, float))) for label in labels}
        return sorted(labels, key=lambda label: totals[label], reverse=sort.startswith("-"))
    return sorted(labels, reverse=sort == "descending")
