"""The form the data calls for: the mark, which column plays which role, the orientation, the sort, and the room the bars need.

The model chooses what the chart is about — the columns, the intent, the words. Everything a data type or a cardinality decides
is decided here, so the same table always gets the same form. The lineage is Mackinlay's expressiveness and effectiveness rules
as Draco encodes them; the numbers taken from Draco are marked at their constants.
"""

import re
from dataclasses import dataclass, field

from data_agents.charts import house_style, presentation
from data_agents.contracts import ChartSpec, Intent

# Year, month and day buckets as SQL returns them, and the timestamp a date column holds when nobody bucketed it, which is the
# common case in a database not written for us (Chinook's InvoiceDate) and used to be drawn as unordered categories.
DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?)?)?$")
IDENTIFIER = {"id", "key", "code"}  # the last word of a column whose numbers name rows instead of measuring them
MANY_CATEGORIES = 6      # bars flip horizontal past this, or sooner if the labels do not fit their slots
BAND = 22                # pixels one horizontal bar needs before its label touches the next
SERIES_BAND = 6          # and each grouped bar inside that band needs this much
MAX_HEIGHT = 900         # taller than this is a table with decoration, not a chart
MAX_BARS = MAX_HEIGHT // BAND  # 40; Draco penalises a discrete axis past 50
MATRIX_SERIES = 3        # more series than this over more categories than fit: a grid of cells, not a row of hairlines
MAX_SLICES = 3           # the House Style's own reason for refusing a pie is that it reads worse than bars past two or three parts
BINS = 10                # Draco: penalise more than 12 buckets and fewer than 7
MIN_BIN_VALUES = 15      # Draco: a column with fewer distinct values than this is not binned; its values are the buckets


def field_type(values: list) -> str:
    values = [v for v in values if v is not None]
    if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "quantitative"
    if values and all(isinstance(v, str) and DATE.match(v) for v in values):
        return "temporal"
    return "nominal"


@dataclass
class Form:
    mark: str                    # bar, line, area, point, rect
    x: str
    y: str                       # the measure, except on a rect, where both axes are categories and colour is the measure
    color: str | None
    data: list[dict]             # capped and reordered here, so what is drawn is what was decided
    horizontal: bool = False
    sort: str | list | None = None  # a Sort, an explicit order of labels, or None for the order the analysis returned
    grouped: bool = False
    stacked: bool = False
    counted: bool = False        # the measure axis is a count of rows, not a column
    binned: bool = False         # and the value axis is cut into buckets
    categorical: bool = False    # the label axis is drawn as categories although its values are dates: a ranking was asked
    height: int = house_style.HEIGHT
    notes: list[str] = field(default_factory=list)


def choose(spec: ChartSpec, intent: Intent, data: list[dict], types: dict[str, str], truncated: bool = False) -> Form:
    x, y, color = spec.x, spec.y, spec.color
    notes = [f"The result was capped at {len(data)} rows."] if truncated else []
    mark = spec.chart_type
    # A histogram of a table that is already one row per bucket is a row of bars of height one, so it takes a table with enough
    # rows to have a spread; anything smaller is the bar chart of counts it already is.
    if (mark == "histogram" and len(data) >= MIN_BIN_VALUES) or (intent == "distribution" and mark == "bar" and _raw_values(x, data)):
        return _histogram(x, y, data, notes)
    if mark == "histogram":
        mark = "bar"
    if mark in ("line", "area") and types[x] == "nominal":
        mark = "bar"  # a line asserts an order to travel along; categories have none, so every categorical rule below applies
    # A ranking asked over a time axis makes the axis a ranking rather than a timeline: the dates become categories and the
    # bars take the order asked for. Only a bar, only when the dates fit as bars; a trend line keeps its timeline and the
    # order is dropped as before, and past MAX_BARS the ranking would cut rows the table holds.
    categorical = bool(spec.sort) and mark == "bar" and types[x] == "temporal" and len({r[x] for r in data}) <= MAX_BARS
    if categorical:
        types = types | {x: "nominal"}
    if color in (x, y):
        color = None  # one column never plays two channels; the validator refused it once and this is the backstop
    if color:
        series, categories = {r[color] for r in data}, {r[x] for r in data}
        # More series than the palette has colours makes Vega recycle them, and two series in one colour is a lie. When the
        # category axis has room, the roles swap: the same data, drawn so that every series gets its own colour.
        if len(series) > len(house_style.PALETTE) >= len(categories):
            x, color, series, categories = color, x, categories, series
        if not _colour_informs(color, x, data, types[x] != "quantitative"):
            color = None  # colour has to tell the reader something: not x again, not a legend of one value
        # Only a bar degenerates into hairlines when its series multiply; lines and points overlap in the same space, so a
        # crowded one of those is left to `chartable.unreadable` rather than turned into a grid of cells. A cell grid also needs
        # two categorical axes: asked for over two measures, which the benchmark caught the model doing once, it is not one.
        elif (types[x] != "quantitative" and types[color] != "quantitative"
              and (mark == "heatmap" or (mark == "bar" and (len(series) > len(house_style.PALETTE) or _matrix(series, categories, data))))):
            return _heatmap(x, y, color, data, notes)
    if mark == "pie":
        # The House Style refuses a pie because it reads worse than bars for more than two or three parts. That reason does
        # not object below the line, and refusing there anyway was the House Style disagreeing with its own justification.
        # Above it the refusal stands and the parts become bars, which is still what the guidance actually says.
        slices = {r[x] for r in data}
        if types[x] != "quantitative" and 1 < len(slices) <= MAX_SLICES and len(data) == len(slices) and _whole(y, data):
            return Form(mark="arc", x=x, y=y, color=x, data=sorted(data, key=lambda r: -r[y]), notes=notes)
        mark = "bar"
    if mark == "heatmap":
        mark = "bar"  # asked for without a second category, a heatmap is a row of one-cell columns
    if mark == "bar" and types[x] == "quantitative" and len({r[x] for r in data}) > MANY_CATEGORIES and not _identifier(x):
        mark = "point"  # two measures against each other is a scatter; a bar over them is a picket fence with no labels
    return _cartesian(mark, x, y, color, intent, data, types, notes, spec.sort, spec.stacked, categorical)


def _cartesian(mark: str, x: str, y: str, color: str | None, intent: Intent, data: list[dict], types: dict[str, str], notes: list[str],
               asked: str | None = None, stack: bool = False, categorical: bool = False) -> Form:
    labels = {r[x] for r in data}
    sort, horizontal = None, False
    if mark == "bar" and types[x] == "nominal":
        # An order the user asked for in words outranks both the bucket order and the house default, because it is what was
        # asked. Everywhere else it is dropped without a word: a time axis, a scatter and a histogram each order themselves,
        # and there is no reading of "alphabetically" a renderer could apply to them.
        sort = asked or presentation.bucket_order(labels) or ("measure desc" if intent in ("comparison", "share") else None)
        # Bars go horizontal when there are too many of them, and when a label is too wide for the slot a fixed axis gives it:
        # on y each label has its own row and the height can grow, on x they overlap into a smear.
        horizontal = len(labels) > MANY_CATEGORIES or not presentation.labels_fit(labels, house_style.WIDTH)
    height = house_style.HEIGHT
    if horizontal:
        if len(labels) > MAX_BARS:
            notes.append(f"Showing the {MAX_BARS} largest of {len(labels)}.")
            data, labels = _largest(x, y, data, labels)
        band = max(BAND, SERIES_BAND * len({r[color] for r in data})) if color else BAND
        height = min(MAX_HEIGHT, max(height, band * len(labels)))
    several = bool(color) and mark == "bar" and len(data) > len(labels)  # several rows per category need a slot each
    # Every bar reads against zero, so the House Style groups by default; parts of one whole are the exception, because there
    # the whole is the finding and a stack is the only way to show it. The Visualization Agent says so directly (`stacked`),
    # because the intent alone said it on 1 of 34 multi-series bars the ground truth wanted stacked.
    whole = stack or intent == "share"
    return Form(mark=mark, x=x, y=y, color=color, data=data, horizontal=horizontal, sort=sort, height=height, notes=notes,
                grouped=several and not whole, stacked=several and whole, categorical=categorical)


def _whole(measure: str, data: list[dict]) -> bool:
    """Slices are parts of a whole, so a negative one has no angle to draw and the total has to be something."""
    values = [r[measure] for r in data]
    return all(isinstance(v, (int, float)) and v >= 0 for v in values) and sum(values) > 0


def _heatmap(category: str, measure: str, series: str, data: list[dict], notes: list[str]) -> Form:
    """Two categories crossed with one measure. Colour reads magnitude less accurately than length (Cleveland & McGill 1984),
    but a grouped bar past a few series gives each bar six pixels, and six pixels of length read worse than a cell of colour."""
    rows = {r[category] for r in data}
    if len(rows) > MAX_BARS:
        notes.append(f"Showing the {MAX_BARS} largest of {len(rows)}.")
        data, rows = _largest(category, measure, data, rows)
    return Form(mark="rect", x=series, y=category, color=measure, data=data, sort="measure desc", notes=notes,
                height=min(MAX_HEIGHT, max(house_style.HEIGHT, BAND * len(rows))))


def _largest(label: str, measure: str, data: list[dict], labels: set) -> tuple[list[dict], set]:
    """The MAX_BARS labels with the largest totals, every row of each kept: a cut by rows would leave a grouped bar with some
    series missing under some labels, which reads as a zero that is not there."""
    total = {v: sum(r[measure] for r in data if r[label] == v and isinstance(r[measure], (int, float))) for v in labels}
    keep = set(sorted(labels, key=lambda v: -total[v])[:MAX_BARS])
    return [r for r in data if r[label] in keep], keep


def _histogram(label: str, measure: str, data: list[dict], notes: list[str]) -> Form:
    """The spread of one measure over the rows. Bucketing is the renderer's, so the buckets are ordered and evenly wide."""
    distinct = len({r[measure] for r in data})
    return Form(mark="bar", x=label, y=measure, color=None, data=data, counted=True, binned=distinct >= MIN_BIN_VALUES, notes=notes)


def _matrix(series: set, categories: set, data: list[dict]) -> bool:
    """A grid, not a row of bar groups: more series than a group can hold, over more categories than a fixed axis can label,
    and dense enough that most cells hold a number. A sparse grid drawn as cells is mostly blank, and the bars are honest."""
    return len(series) > MATRIX_SERIES and len(categories) > MANY_CATEGORIES and len(data) >= len(series) * len(categories) / 2


def _raw_values(label: str, data: list[dict]) -> bool:
    """One row per thing and more of them than a row of bars can carry: the finding is the spread, not each row's own bar."""
    return len(data) > MAX_BARS and len({r[label] for r in data}) == len(data)


def _colour_informs(color: str, x: str, data: list[dict], labelled: bool) -> bool:
    """Not x again, and never a legend of one value. One entry per row is noise beside an axis that already names the rows,
    and the only identity a scatter has, both of whose axes are measures: dropping it left identical dots on 21 of VisEval's
    32 grouped scatters."""
    series = len({r[color] for r in data})
    return color != x and 1 < series and (series < len(data) or not labelled)


def _identifier(column: str) -> bool:
    return presentation.axis_title(column).split()[-1].lower() in IDENTIFIER
