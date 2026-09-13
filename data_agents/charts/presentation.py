"""What a chart's numbers say: the unit a measure carries, the magnitude it is drawn at, its tick format, and the room labels need.

The form layer decides what the chart is; this decides what is written on it. Nothing here is the model's choice. Every
threshold carries, at its own constant, the measurement or the source behind it.
"""

import math
import re
import unicodedata
from dataclasses import dataclass

CHAR_PX = 6.2   # width of one narrow label character at the House Style's 11px face
WIDE = {"W", "F"}  # East Asian full-width: one CJK glyph is about two narrow advances at the same size
LABEL_GAP = 8   # pixels between two axis labels before they read as one
SLANT = -45     # degrees a label on x is turned when it does not fit its slot upright and the axis cannot flip
LINE_PX = 11    # the height of a label line at the House Style's face, which a slanted label adds to its footprint
TICK_CHARS = 6  # a tick label wider than "100,000" is abbreviated instead: 5.3M, not 5,286,953
# Where a column name breaks into words: before a capital that follows a lowercase or digit, and before the last capital of a run
# that starts a word, so OrdersHandled reads as two words and TotalUSD keeps its acronym.
WORD_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
BUCKET = re.compile(r"^[(\[]?\s*(?P<under>[<≤])?\s*(?P<n>-?\d+(?:[.,]\d+)?)")

DURATION = [("milliseconds", 1), ("seconds", 1_000), ("minutes", 60_000), ("hours", 3_600_000), ("days", 86_400_000)]
BYTES = [("bytes", 1), ("kilobytes", 1024), ("megabytes", 1024**2), ("gigabytes", 1024**3)]
# The word in a column name -> the ladder its numbers sit on, and the rung they are stored at. Only what the name says: Sakila
# stores film length in minutes under a column called `length`, and inventing a unit is worse than leaving the number bare.
UNITS = {"millisecond": (DURATION, 1), "ms": (DURATION, 1), "msec": (DURATION, 1), "second": (DURATION, 1_000),
         "sec": (DURATION, 1_000), "minute": (DURATION, 60_000), "min": (DURATION, 60_000), "hour": (DURATION, 3_600_000),
         "byte": (BYTES, 1)}
PERCENT = {"percent", "percentage", "pct"}
# Date buckets as SQL returns them -> the tick interval, the label format and its width in characters, within one year and across years.
GRAIN = {4: ("year", "%Y", 4, "%Y", 4), 7: ("month", "%b", 3, "%b %Y", 8), 10: ("day", "%-d %b", 6, "%-d %b %Y", 11),
         16: ("day", "%-d %b", 6, "%-d %b %Y", 11), 19: ("day", "%-d %b", 6, "%-d %b %Y", 11)}
# Parsed in local time, so ticks land on the buckets (ISO strings would parse as UTC). The last two are an unbucketed date
# column, which SQLite stores as text with a time on the end.
PARSE = {4: "%Y", 7: "%Y-%m", 10: "%Y-%m-%d", 16: "%Y-%m-%d %H:%M", 19: "%Y-%m-%d %H:%M:%S"}


def axis_title(column: str) -> str:
    """A column name as words. The databases disagree on spelling (film_count, OrdersHandled), the axis should not."""
    return WORD_BREAK.sub(" ", column).replace("_", " ")


def text_width(value) -> float:
    """How wide a label is drawn, counting a full-width glyph as two narrow ones.

    An estimate, not a measurement: the SVG `vl_convert` emits carries each label's position and font but no text extent, so
    nothing short of a font-metrics dependency can measure it. Counting advances removes the one 2x error that was found:
    fifteen CJK characters estimated at 101 pixels in a 107-pixel slot, drawn as a smear.
    """
    return sum(2 if unicodedata.east_asian_width(c) in WIDE else 1 for c in str(value)) * CHAR_PX


def labels_fit(labels, width: float, slanted: bool = False) -> bool:
    """Every label has to fit its own slot, not the axis as a whole: the axis divides evenly, so the widest label is the constraint.
    Turned 45 degrees a label's footprint along the axis is its width and its height each scaled by cos 45°, so about 0.71 of
    the width plus 8 pixels: the rule that stands in for measuring the rendered text, and still an estimate like the width."""
    widest = max((text_width(v) for v in labels), default=0)
    footprint = (widest + LINE_PX) * 0.71 if slanted else widest
    return not labels or footprint + LABEL_GAP <= width / len(labels)


def tick_step(count: int, chars: int, width: float) -> int:
    """Label every k-th bucket, k being the smallest number that makes the labels fit."""
    return max(1, math.ceil(count / max(1, int(width // (chars * CHAR_PX + LABEL_GAP)))))


@dataclass
class Measure:
    """How one numeric column is shown: what the axis is called, what to divide the stored numbers by, and how a tick reads."""

    title: str
    factor: float = 1.0
    format: str | None = None    # d3 number format; None keeps the House Style's
    ticks: list[int] | None = None  # exact tick positions, where leaving them to Vega would put a tick between two integers


def measure(column: str, values: list) -> Measure:
    numbers = [abs(float(v)) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    words = axis_title(column).split()
    # Only the last word can be the unit: English puts it after the measure (run_seconds, track_share_percent), and in front it
    # is an aggregate. Measured over the stored benchmark runs: every leading "min" (min_price, Min_Age) was a minimum, not minutes.
    named = [w for w in words[-1:] if _key(w) in UNITS or _key(w) in PERCENT]
    rest = " ".join(words[:-1]) if named else ""
    unit, factor = None, 1.0
    if named:
        first = _key(named[0])
        unit, factor = (_rung(*UNITS[first], numbers) if first in UNITS else ("%", 1.0))
    scaled = [n / factor for n in numbers] or [0.0]
    integral = factor == 1.0 and all(n.is_integer() for n in numbers)
    wide = len(f"{max(scaled):,.0f}") > TICK_CHARS  # the widest tick label the axis would have to carry
    return Measure(title=_title(rest, unit, words), factor=factor,
                   format="~s" if wide else (",d" if integral else None),
                   ticks=_integer_ticks(scaled) if integral and not wide else None)


def _integer_ticks(values: list[float]) -> list[int] | None:
    """One tick per whole number. A count has no half, and Vega will happily offer 1.5 customers: it reads `tickMinStep` as a
    hint and nices the step anyway, so the positions are given to it outright."""
    low, high = min(0, min(values)), max(values)
    return [n for n in range(int(low), int(high) + 1)] if high - low <= 12 else None


def _key(word: str) -> str:
    """The word as the unit table spells it: plural or singular, but never "ms" cut down to "m"."""
    word = word.lower()
    return word if word in UNITS or word in PERCENT else word.rstrip("s")


def _title(rest: str, unit: str | None, words: list[str]) -> str:
    if unit is None:
        return " ".join(words)
    return f"{rest} ({unit})" if rest else ("Percent" if unit == "%" else unit.capitalize())


def _rung(ladder: list[tuple[str, int]], base: int, numbers: list[float]) -> tuple[str, float]:
    """The smallest rung on which the largest value still reads in three digits: 5,286,953 milliseconds is 88 minutes, which is
    how long a song is, and neither 5,286,953 nor 1.47 hours is."""
    top = (max(numbers) if numbers else 1.0) * base
    name, size = next(((n, s) for n, s in ladder if top / s < 1000), ladder[-1])
    return name, size / base


@dataclass
class TimeAxis:
    parse: str
    format: str
    interval: str
    step: int  # label every step-th bucket


def time_axis(values: list, width: float) -> TimeAxis:
    """Ticks on the buckets the data actually holds: twelve months get twelve labels if twelve labels fit, and six if not."""
    text = [str(v) for v in values]
    interval, short, short_chars, across, across_chars = GRAIN[len(text[0])]
    many_years = interval != "year" and len({t[:4] for t in text}) > 1
    fmt, chars = (across, across_chars) if many_years else (short, short_chars)
    return TimeAxis(parse=f"date:'{PARSE[len(text[0])]}'", format=fmt, interval=interval, step=tick_step(len(set(text)), chars, width))


def bucket_order(labels) -> list | None:
    """Buckets are ordinal even when they are text ('<60 min' before '60-89 min'), so they are never sorted by magnitude.
    None when the labels are not buckets, which is every ordinary category."""
    keys = {}
    for label in labels:
        if not (found := BUCKET.match(str(label))):
            return None
        keys[label] = (float(found["n"].replace(",", "")), -1 if found["under"] else 0, str(label))
    return sorted(keys, key=keys.get)


def indistinct(values: list, length: float, zero: bool) -> bool:
    """Whether two bars would be drawn within three pixels of each other, which reads as equal when it is not (eight tracks of
    48-and-a-bit minutes). Three pixels is the smallest difference in length worth calling visible; the sourced claim under it
    is only Cleveland & McGill's, that length is judged by comparison.
    """
    numbers = sorted({float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)})
    if len(numbers) < 2:
        return False
    span = (max(numbers) if zero else max(numbers) - min(numbers)) or 1.0
    return min(b - a for a, b in zip(numbers, numbers[1:])) / span * length < 3


def zoom_note(values: list) -> str | None:
    """A zoomed measure axis shows the change but hides the size it is a change in, which reads as a cliff. Say so on the chart.

    Fires only when the hidden part of the scale is larger than the drawn part, which is when the exaggeration is material.
    """
    numbers = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers or not (min(numbers) > max(numbers) - min(numbers) > 0):
        return None
    return f"The measure axis is zoomed to {min(numbers):,.4g}–{max(numbers):,.4g} and does not start at zero."
