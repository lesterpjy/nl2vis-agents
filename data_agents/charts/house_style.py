"""The House Style: one theme applied after the Visualization Agent is done. Invented brand, provisional until the style is decided."""

import hashlib

BRAND = "Data Agents"
FONT = "Helvetica"
WIDTH, HEIGHT = 640, 360
SURFACE = "#fcfcfb"
INK, INK_SECONDARY, INK_MUTED = "#0b0b0b", "#52514e", "#9a9891"
GRID = "#e8e7e3"

# Categorical slots in fixed order: validated for colour-vision deficiency on the light surface (dataviz validator).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
# Sequential ramp: one hue, light to dark, for magnitude.
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

CONFIG = {
    "background": SURFACE,
    "font": FONT,
    "view": {"stroke": None},
    "range": {"category": PALETTE, "ordinal": SEQUENTIAL, "heatmap": SEQUENTIAL},
    "title": {"anchor": "start", "fontSize": 17, "fontWeight": 600, "color": INK, "subtitleFontSize": 12,
              "subtitleColor": INK_SECONDARY, "subtitlePadding": 4, "offset": 16},
    "axis": {"labelFontSize": 11, "titleFontSize": 11, "labelColor": INK_SECONDARY, "titleColor": INK_SECONDARY,
             "gridColor": GRID, "domain": False, "ticks": False, "labelPadding": 6, "titlePadding": 10},
    "axisX": {"grid": False, "labelAngle": 0},
    "legend": {"labelFontSize": 11, "titleFontSize": 11, "labelColor": INK_SECONDARY, "titleColor": INK_SECONDARY, "orient": "top", "symbolType": "circle"},
    "numberFormat": ",.2~f",
    "timeFormat": "%b %Y",
    "bar": {"color": PALETTE[0], "cornerRadiusEnd": 4, "discreteBandSize": {"band": 0.55}},  # thin marks, rounded data ends
    "line": {"color": PALETTE[0], "strokeWidth": 2, "point": {"filled": True, "size": 48}},
    "area": {"color": PALETTE[0], "opacity": 0.35, "line": {"strokeWidth": 2}},
    "point": {"color": PALETTE[0], "filled": True, "size": 64},
    "rect": {"cornerRadius": 2},  # heatmap cells; their colour is the sequential ramp, which carries magnitude
    "text": {"color": INK_SECONDARY, "fontSize": 10},
}


def caption(source: str, sql: str = "") -> str:
    """Source line every chart carries: where the numbers came from, and a hash of the SQL when there was SQL to hash."""
    provenance = f" · SQL {hashlib.sha1(sql.encode()).hexdigest()[:8]}" if sql else ""
    return f"Source: {source}{provenance} · {BRAND}"
